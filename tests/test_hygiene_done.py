"""RED-first: the hygiene agent's Todo-and-proofs lane (DRE-5411).

`scripts/hygiene_done.py` reads the cards in `Todo`, `In Progress`, `In Review`
and `Hand-work` once and closes what the evidence says is done, under four
rules, each with the cause line the core's (tag, cause) key reads:

  1. a card whose pull request merged but never closed — a receipt naming the
     merge's own PT time, then Done; a `no-code` card is a left row instead;
  2. a `PROOF:` card whose merged record holds a criterion table with every
     non-closing row met — a receipt naming the record and the rows met, then
     Done; any other record is a left row "ready for the CEO";
  3. a `PROOF:` card whose parent epic is `Canceled` or `Duplicate` — Canceled;
  4. a Todo build card re-dispatched at least twice with no run — one note
     naming the cause, at most once per card per PT day, and a left row.

The fixture is `tests/fixtures/hygiene-done-2026-09-30.json`, in the shape every
hygiene lane fixture takes: `lanes`, `prs` and a `gh` map from an argv joined
with single spaces to the stdout the fake `ctx.gh` answers. Its three proof
records, and the thirteen the record-by-record test reads, are copies of
`docs/*proof*.md` at bureau-pipeline `main` commit
2e5aefaf7505cb8450208ced5e28334bea134512 (2026-09-30 18:36 PT), byte for byte,
in `tests/fixtures/hygiene-done-records/` — never a glob of the live checkout.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hygiene_done.py -v
"""
from __future__ import annotations

import ast
import base64
import copy
import importlib.util
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hygiene  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "hygiene_done.py"
FIXTURE = ROOT / "tests" / "fixtures" / "hygiene-done-2026-09-30.json"
RECORDS = ROOT / "tests" / "fixtures" / "hygiene-done-records"

#: 01:40 UTC on 2026-10-01 is 18:40 on 2026-09-30 in Pacific Daylight Time.
NOW = datetime(2026, 10, 1, 1, 40, tzinfo=UTC)
PASS_CLOCK = "18:40"
HOME = "dreadnought-foundry"
BP = "dreadnought-foundry/bureau-pipeline"
AB = "dreadnought-foundry/agent-bureau"
FIELDS = "number,url,headRefName,state,mergedAt,files"
REDISPATCH = "🧹 Reconcile: card sat in Todo with no run — re-dispatched."
CEO_RULE = "if the cards are proven with evidence then just approve them"


def load_lane():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("hygiene_done", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lane = load_lane()


def need_lane():
    assert lane is not None, f"{MODULE_PATH.name} does not exist yet"
    return lane


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FixtureGh:
    """The read-only `gh` a lane sees, answering ONLY from the fixture's `gh`
    map — a read the map does not hold fails the test. The core's open
    pull-request list is answered from the fixture's `prs`."""

    def __init__(self, doc: dict):
        self.answers = dict(doc["gh"])
        self.prs = doc["prs"]
        self.calls: list = []

    def __call__(self, argv):
        argv = [str(a) for a in argv]
        refusal = hygiene.read_gh_refusal(argv)
        assert refusal is None, f"the lane asked ctx.gh for a write: {refusal}"
        if argv[:3] == ["gh", "pr", "list"] and "open" in argv:
            return json.dumps(self.prs.get(argv[argv.index("--repo") + 1], []))
        key = " ".join(argv)
        self.calls.append(key)
        if key not in self.answers:
            raise AssertionError(f"the fixture's gh map has no answer for {key!r}")
        answer = self.answers[key]
        if isinstance(answer, Exception):
            raise answer
        return answer


def context(doc, *, now=NOW, dry_run=False):
    gh = FixtureGh(doc)
    ctx = hygiene.make_context(HOME, gh=gh, linear=lambda q, v=None: {},
                               dry_run=dry_run, now=now, summary_card="DRE-900")
    return ctx, gh


def plan(doc=None, *, now=NOW):
    doc = doc if doc is not None else fixture()
    ctx, gh = context(doc, now=now)
    board = hygiene.Board(lanes=doc["lanes"], prs=doc["prs"])
    return need_lane().plan(board, ctx), ctx, gh


def actions(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Action)
            and (target is None or i.target == target)]


def lefts(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Left)
            and (target is None or i.target == target)]


def card_of(doc, ident):
    return next(c for cards in doc["lanes"].values() for c in cards
                if c["identifier"] == ident)


def lane_of(doc, ident):
    return next(name for name, cards in doc["lanes"].items()
                if any(c["identifier"] == ident for c in cards))


def move(doc, ident, to):
    card = card_of(doc, ident)
    doc["lanes"][lane_of(doc, ident)].remove(card)
    doc["lanes"].setdefault(to, []).append(card)
    card["state"] = {"name": to}


def lookup_key(repo, ident):
    return " ".join(["gh", "pr", "list", "--repo", repo, "--state", "all", "--limit", "30",
                     "--json", FIELDS, "--search", f"head:agent/{ident}"])


def runs_key(repo, stub):
    return " ".join(["gh", "run", "list", "--repo", repo, "--workflow", stub, "--json",
                     "databaseId,status,conclusion,createdAt,event", "--limit", "50"])


def contents_key(repo, path):
    return f"gh api repos/{repo}/contents/{path}?ref=main"


def contents_answer(path, text):
    return json.dumps({"type": "file", "encoding": "base64", "path": path,
                       "content": base64.encodebytes(text.encode("utf-8")).decode("ascii")})


def merged_pr(repo, number, ident, merged_at, paths):
    return {"number": number, "url": f"https://github.com/{repo}/pull/{number}",
            "headRefName": f"agent/{ident}-x", "state": "MERGED", "mergedAt": merged_at,
            "files": [{"path": p, "additions": 1, "deletions": 0} for p in paths]}


def comment_of(action):
    return next(w for w in action.writes if w.kind == "linear_comment").body


def states(action):
    return [w.lane for w in action.writes if w.kind == "linear_state"]


def first_line(body):
    return body.splitlines()[0]


def add_comment(card, body, at):
    card["comments"]["nodes"].insert(0, {"body": body, "createdAt": at,
                                         "user": {"id": "user-7c1e"}})


# --------------------------------------------------------------------------- #
# the lane's shape                                                             #
# --------------------------------------------------------------------------- #


class TestTheLane:
    def test_its_heading_is_todo_and_proofs(self):
        assert need_lane().LANE == "Todo and proofs"

    def test_the_core_discovers_it_by_the_glob(self):
        assert "hygiene_done" in [m.__name__ for m in hygiene.discover()]

    def test_every_row_it_returns_takes_its_heading(self):
        items, _ctx, _gh = plan()
        assert items
        assert {i.lane for i in items} == {"Todo and proofs"}

    def test_the_named_constants_are_the_cards_words(self):
        mod = need_lane()
        assert mod.OWN_PROOF == "DRE-5412"
        assert mod.CLOSING_ROW_WORDS == ("merged", "on main", "the ceo", "close")
        assert mod.MET_WORDS == ("met", "holds", "observed", "proven", "pass", "yes")

    def test_a_card_in_hand_work_is_read(self):
        doc = fixture()
        move(doc, "DRE-9102", "Hand-work")
        items, _ctx, _gh = plan(doc)
        assert [a.act for a in actions(items, "DRE-9102")] == ["hygiene-card-close"]

    def test_a_lane_it_does_not_read_is_never_touched(self):
        doc = fixture()
        move(doc, "DRE-9101", "Green Light")
        move(doc, "DRE-9104", "Triage")
        items, _ctx, gh = plan(doc)
        assert not actions(items, "DRE-9101") and not lefts(items, "DRE-9101")
        assert not actions(items, "DRE-9104")
        assert not any("DRE-9101" in c for c in gh.calls)


# --------------------------------------------------------------------------- #
# (1) merged but never closed                                                  #
# --------------------------------------------------------------------------- #


class TestMergedButNeverClosed:
    @pytest.mark.parametrize("ident, cause", [
        ("DRE-9101", "#688 merged 2026-09-30 16:41 PT"),
        ("DRE-9102", "#3101 merged 2026-09-30 13:12 PT"),  # hand-built
    ])
    def test_exactly_two_writes_a_receipt_then_done(self, ident, cause):
        items, _ctx, _gh = plan()
        (action,) = actions(items, ident)
        assert action.act == "hygiene-card-close"
        assert action.cause == cause
        assert [w.kind for w in action.writes] == ["linear_comment", "linear_state"]
        assert states(action) == ["Done"]
        assert first_line(comment_of(action)).startswith(
            f"🧹 hygiene: hyg-card-closed — {cause} · ")
        assert lefts(items, ident) == []

    def test_the_receipt_quotes_the_ceos_standing_rule_and_names_the_pull_request(self):
        items, _ctx, _gh = plan()
        (action,) = actions(items, "DRE-9101")
        body = comment_of(action)
        assert f'"{CEO_RULE}"' in body
        assert f"{BP}#688" in body

    def test_a_no_code_card_with_a_merged_pull_request_is_left(self):
        items, _ctx, _gh = plan()
        assert actions(items, "DRE-9103") == []
        (row,) = lefts(items, "DRE-9103")
        assert "#690" in row.why

    def test_an_open_pull_request_is_not_done(self):
        items, _ctx, _gh = plan()
        assert actions(items, "DRE-9105") == [] and lefts(items, "DRE-9105") == []

    def test_an_epic_is_never_looked_up(self):
        doc = fixture()
        card_of(doc, "DRE-9101")["title"] = "[EPIC] bureau-pipeline: the sweep"
        items, _ctx, gh = plan(doc)
        assert actions(items, "DRE-9101") == [] and lefts(items, "DRE-9101") == []
        assert lookup_key(BP, "DRE-9101") not in gh.calls

    def test_a_card_no_repo_map_names_is_not_looked_up(self):
        doc = fixture()
        card = card_of(doc, "DRE-9101")
        card["labels"]["nodes"] = [n for n in card["labels"]["nodes"]
                                   if not n["name"].startswith("repo:")]
        items, _ctx, gh = plan(doc)
        assert actions(items, "DRE-9101") == []
        assert not any("DRE-9101" in c for c in gh.calls)


# --------------------------------------------------------------------------- #
# (2) a proof proven                                                           #
# --------------------------------------------------------------------------- #


class TestAProofProven:
    def test_a_record_with_every_row_met_closes_the_proof(self):
        items, _ctx, _gh = plan()
        (action,) = actions(items, "DRE-3904")
        cause = "record docs/model-adoption-proof-2026-09.md at #605, 3 rows met"
        assert action.act == "hygiene-proof-close"
        assert action.cause == cause
        assert [w.kind for w in action.writes] == ["linear_comment", "linear_state"]
        assert states(action) == ["Done"]
        assert first_line(comment_of(action)).startswith(f"🧹 hygiene: hyg-proof-closed — {cause} · ")
        assert f'"{CEO_RULE}"' in comment_of(action)
        assert lefts(items, "DRE-3904") == []

    def test_a_record_with_rows_not_met_is_ready_for_the_ceo(self):
        items, _ctx, _gh = plan()
        assert actions(items, "DRE-3646") == []
        (row,) = lefts(items, "DRE-3646")
        assert row.why.startswith("ready for the CEO")
        assert "docs/sweep-spend-proof-2026-09.md" in row.why
        assert "Not observed" in row.why
        assert "Fails as written" in row.why
        assert "Fails — 39, 30, 36" in row.why
        assert "Pass" not in row.why  # the rows that are met are not named

    def test_a_record_with_no_criterion_table_says_so(self):
        items, _ctx, _gh = plan()
        assert actions(items, "DRE-3503") == []
        (row,) = lefts(items, "DRE-3503")
        assert row.why.startswith("ready for the CEO")
        assert "docs/review-turns-proof-2026-09.md" in row.why
        assert "no criterion table" in row.why

    def test_the_record_is_read_at_the_default_branch(self):
        _items, _ctx, gh = plan()
        assert f"gh api repos/{BP}" in gh.calls
        assert contents_key(BP, "docs/model-adoption-proof-2026-09.md") in gh.calls

    def test_a_merged_pull_request_that_touched_no_record_closes_nothing(self):
        doc = fixture()
        pr = json.loads(doc["gh"][lookup_key(BP, "DRE-3904")])
        pr[0]["files"] = [{"path": "scripts/model_adoption.py", "additions": 3, "deletions": 0}]
        doc["gh"][lookup_key(BP, "DRE-3904")] = json.dumps(pr)
        items, _ctx, _gh = plan(doc)
        assert actions(items, "DRE-3904") == []

    def test_a_record_under_architecture_is_a_record(self):
        doc = fixture()
        path = "architecture/model-adoption-proof.md"
        text = (RECORDS / "model-adoption-proof-2026-09.md").read_text(encoding="utf-8")
        doc["gh"][lookup_key(BP, "DRE-3904")] = json.dumps(
            [merged_pr(BP, 605, "DRE-3904", "2026-09-30T21:56:26Z", [path])])
        doc["gh"][contents_key(BP, path)] = contents_answer(path, text)
        items, _ctx, _gh = plan(doc)
        (action,) = actions(items, "DRE-3904")
        assert action.cause == f"record {path} at #605, 3 rows met"

    def test_a_proof_with_no_merged_pull_request_yields_nothing(self):
        doc = fixture()
        doc["gh"][lookup_key(BP, "DRE-3904")] = "[]"
        items, _ctx, _gh = plan(doc)
        assert actions(items, "DRE-3904") == [] and lefts(items, "DRE-3904") == []


class TestTheOwnProof:
    def test_dre_5412_is_left_for_the_ceo_and_never_read(self):
        items, _ctx, gh = plan()
        assert actions(items, "DRE-5412") == []
        (row,) = lefts(items, "DRE-5412")
        assert "the CEO closes it" in row.why
        assert not any("DRE-5412" in c or "hygiene-proof-2026-10" in c for c in gh.calls)

    def test_own_proof_is_the_exclusion(self, monkeypatch):
        # Its record would qualify: with the constant pointed elsewhere, the
        # same card closes — so the identifier, not the record, keeps it open.
        monkeypatch.setattr(need_lane(), "OWN_PROOF", "DRE-0")
        items, _ctx, _gh = plan()
        (action,) = actions(items, "DRE-5412")
        assert action.act == "hygiene-proof-close"


# --------------------------------------------------------------------------- #
# the criterion table, and the thirteen records on main                        #
# --------------------------------------------------------------------------- #


class TestCriterionRows:
    @pytest.mark.parametrize("table, pairs", [
        ("| Criterion | Result |\n|---|---|\n| a thing happened | **Met** (§1) |\n",
         [("a thing happened", "**Met** (§1)")]),
        ("| Acceptance criterion | State |\n|---|---|\n| it ran | **Met** |\n",
         [("it ran", "**Met**")]),
        ("| # | criterion | verdict | rests on |\n|---|---|---|---|\n"
         "| 1 | it was seen | **Pass** — 20, 20, 20 | §2 |\n",
         [("it was seen", "**Pass** — 20, 20, 20")]),
        ("| criterion | reading |\n|---|---|\n| no failures | **yes** — §4 |\n",
         [("no failures", "**yes** — §4")]),
    ])
    def test_the_four_header_shapes_on_main_parse(self, table, pairs):
        assert need_lane().criterion_rows("# Record\n\nProse.\n\n" + table) == pairs

    def test_the_first_table_with_a_criterion_header_is_the_one_read(self):
        text = ("| Run | Started |\n|---|---|\n| 1 | 19:35 |\n\n"
                "| Criterion | Result |\n|---|---|\n| one | Met |\n\n"
                "| Criterion | Result |\n|---|---|\n| two | Not met |\n")
        assert need_lane().criterion_rows(text) == [("one", "Met")]

    def test_a_record_with_no_criterion_table_reads_none(self):
        text = "| Run | Started |\n|---|---|\n| 1 | 19:35 |\n\n- [x] it ran\n"
        assert need_lane().criterion_rows(text) is None

    @pytest.mark.parametrize("result", [
        "**Met** (§1)", "Met, second branch", "**Pass** — 20, 20, 20", "**yes** — §4",
        "Holds", "_observed_ on the run", "**Proven**",
    ])
    def test_a_result_opening_with_a_met_word_qualifies(self, result):
        assert need_lane().row_met(result) is True

    @pytest.mark.parametrize("result", [
        "Not met", "Not observed", "Partly met", "Fails", "NO.", "pending",
        "released: proven", "Metric unchanged", "Passing later", "",
    ])
    def test_anything_else_does_not(self, result):
        assert need_lane().row_met(result) is False

    @pytest.mark.parametrize("criterion", [
        "Merged to `main` through an operator-opened PR",
        "The record is on `main`",
        "The CEO has read the merged record",
        "the operator closes the card",
    ])
    def test_a_closing_row_is_skipped(self, criterion):
        assert need_lane().is_closing_row(criterion) is True
        text = f"| Criterion | Result |\n|---|---|\n| it ran | Met |\n| {criterion} | Open |\n"
        reading = need_lane().reading(text)
        assert reading.unmet == [] and len(reading.met) == 1


#: The thirteen records at 2e5aefaf, and what the rule makes of each. The
#: model-adoption record's table has six rows: its own merge and the CEO's
#: step are closing rows, and so is "ends with `What the CEO sees`" — it names
#: the CEO — which leaves three judged, all met.
CLOSES = {"model-adoption-proof-2026-09.md": 3, "seam-proof-dre3244.md": 4}
NOT_MET = {
    "linear-identities-proof-2026-09.md": ["NO", "NO"],
    "planner-queue-proof-2026-10.md": ["Not met"],
    "release-decision-proof-2026-09.md": ["released: proven"],
    "reviewer-environment-hold-proof.md": ["Partly observed"],
    "stale-merge-ref-proof.md": ["Not met", "Not observable", "Not observable", "Partly met"],
    "sweep-spend-proof-2026-09.md": ["Not observed", "Fails as written", "Fails"],
}
NO_TABLE = {
    "groomer-two-lists-proof.md", "rereview-continuation-proof-2026-09.md",
    "review-rerun-proof-2026-09.md", "review-turns-proof-2026-09.md",
    "turn-cap-proof-2026-09.md",
}


def _plain(cell):
    return cell.replace("*", "").strip()


def records_doc():
    """One `PROOF:` card per record, each whose merged pull request added it."""
    doc = fixture()
    doc["lanes"] = {"Todo": []}
    template = card_of(fixture(), "DRE-3646")
    for i, path in enumerate(sorted(RECORDS.iterdir())):
        ident = f"DRE-96{i:02d}"
        card = copy.deepcopy(template)
        card.update(identifier=ident, id=f"issue-{ident.lower()}")
        doc["lanes"]["Todo"].append(card)
        doc["gh"][lookup_key(BP, ident)] = json.dumps(
            [merged_pr(BP, 800 + i, ident, "2026-09-30T20:00:00Z", [f"docs/{path.name}"])])
        doc["gh"][contents_key(BP, f"docs/{path.name}")] = contents_answer(
            f"docs/{path.name}", path.read_text(encoding="utf-8"))
    return doc


class TestTheThirteenRecordsOnMain:
    def test_the_copies_are_the_thirteen(self):
        assert sorted(p.name for p in RECORDS.iterdir()) == sorted(
            [*CLOSES, *NOT_MET, *NO_TABLE])
        assert len(CLOSES) + len(NOT_MET) + len(NO_TABLE) == 13

    @pytest.mark.parametrize("name", sorted(CLOSES))
    def test_exactly_two_close(self, name):
        reading = need_lane().reading((RECORDS / name).read_text(encoding="utf-8"))
        assert reading.rows is not None and reading.unmet == [] and reading.met
        assert len(reading.met) == CLOSES[name]

    @pytest.mark.parametrize("name", sorted(NOT_MET))
    def test_exactly_six_are_left_with_the_row_not_met_named(self, name):
        reading = need_lane().reading((RECORDS / name).read_text(encoding="utf-8"))
        assert reading.rows is not None
        assert len(reading.unmet) == len(NOT_MET[name])
        for (_criterion, result), opening in zip(reading.unmet, NOT_MET[name]):
            assert _plain(result).startswith(opening), result

    @pytest.mark.parametrize("name", sorted(NO_TABLE))
    def test_exactly_five_have_no_criterion_table(self, name):
        assert need_lane().reading((RECORDS / name).read_text(encoding="utf-8")).rows is None

    def test_through_the_lane_record_by_record(self):
        doc = records_doc()
        items, _ctx, _gh = plan(doc)
        by_name = {json.loads(doc["gh"][lookup_key(BP, c["identifier"])])[0]["files"][0]["path"]
                   .split("/")[-1]: c["identifier"] for c in doc["lanes"]["Todo"]}
        closed = {n for n, i in by_name.items()
                  if [a.act for a in actions(items, i)] == ["hygiene-proof-close"]}
        assert closed == set(CLOSES)
        for name in NOT_MET:
            (row,) = lefts(items, by_name[name])
            assert actions(items, by_name[name]) == []
            assert row.why.startswith("ready for the CEO") and f"docs/{name}" in row.why
            for opening in NOT_MET[name]:
                assert opening in row.why
        for name in NO_TABLE:
            (row,) = lefts(items, by_name[name])
            assert actions(items, by_name[name]) == []
            assert "no criterion table" in row.why

    @pytest.mark.parametrize("name", sorted(p.name for p in RECORDS.iterdir()))
    def test_no_checkbox_is_read(self, name):
        text = (RECORDS / name).read_text(encoding="utf-8")
        flipped = (text.replace("- [ ]", "- [\0]").replace("- [x]", "- [ ]")
                   .replace("- [X]", "- [ ]").replace("- [\0]", "- [x]"))
        flipped += "\n\n## Criteria\n\n- [x] every criterion is met\n- [ ] one is not\n"
        before, after = need_lane().reading(text), need_lane().reading(flipped)
        assert (before.rows, before.met, before.unmet) == (after.rows, after.met, after.unmet)

    def test_a_record_of_checked_boxes_alone_has_no_criterion_table(self):
        text = "# Proof\n\n## Acceptance criteria\n\n- [x] it ran\n- [x] it was seen\n"
        assert need_lane().reading(text).rows is None


# --------------------------------------------------------------------------- #
# (3) a superseded proof                                                       #
# --------------------------------------------------------------------------- #


class TestASupersededProof:
    def test_a_proof_under_a_canceled_epic_is_canceled(self):
        items, _ctx, gh = plan()
        (action,) = actions(items, "DRE-9104")
        assert action.act == "hygiene-card-cancel"
        assert action.cause == "parent DRE-9120 is Canceled"
        assert [w.kind for w in action.writes] == ["linear_comment", "linear_state"]
        assert states(action) == ["Canceled"]
        assert first_line(comment_of(action)).startswith(
            "🧹 hygiene: hyg-card-canceled — parent DRE-9120 is Canceled · ")
        assert not any("DRE-9104" in c for c in gh.calls)

    def test_a_duplicate_epic_reads_the_same(self):
        doc = fixture()
        card_of(doc, "DRE-9104")["parent"]["state"] = {"name": "Duplicate"}
        items, _ctx, _gh = plan(doc)
        (action,) = actions(items, "DRE-9104")
        assert action.cause == "parent DRE-9120 is Duplicate"

    def test_a_build_card_under_a_canceled_epic_is_not_this_rule(self):
        doc = fixture()
        card_of(doc, "DRE-9105")["parent"] = {"identifier": "DRE-9120",
                                             "state": {"name": "Canceled"}}
        items, _ctx, _gh = plan(doc)
        assert actions(items, "DRE-9105") == []


# --------------------------------------------------------------------------- #
# (4) a Todo build card with no run                                            #
# --------------------------------------------------------------------------- #


class TestATodoCardWithNoRun:
    def test_two_redispatches_and_no_run_name_the_cause_once(self):
        items, _ctx, _gh = plan()
        (action,) = actions(items, "DRE-9107")
        cause = "no run after the re-dispatch at 2026-09-30 17:16 PT"
        assert action.act == "hygiene-cause-name"
        assert action.cause == cause
        assert [w.kind for w in action.writes] == ["linear_comment"]
        body = comment_of(action)
        assert first_line(body).startswith(f"🧹 hygiene: hyg-cause-named — {cause} · ")
        assert "re-dispatched 2 times" in body.splitlines()[1]
        (row,) = lefts(items, "DRE-9107")
        assert row.recommendation

    def test_a_queued_run_means_it_is_about_to_build(self):
        for status in ("queued", "in_progress"):
            doc = fixture()
            key = runs_key(BP, "self-agent-task.yml")
            runs = json.loads(doc["gh"][key])
            runs.insert(0, {"databaseId": 37100000009, "status": status, "conclusion": None,
                            "createdAt": "2026-10-01T00:17:02Z", "event": "repository_dispatch"})
            doc["gh"][key] = json.dumps(runs)
            items, _ctx, _gh = plan(doc)
            assert actions(items, "DRE-9107") == [] and lefts(items, "DRE-9107") == [], status

    def test_a_failed_run_names_its_id_and_failing_step(self):
        items, _ctx, gh = plan()
        (action,) = actions(items, "DRE-9109")
        assert action.cause == "run 37200000002 failed at step Mint the App token"
        assert [w.kind for w in action.writes] == ["linear_comment"]
        assert len(lefts(items, "DRE-9109")) == 1
        assert f"gh run view 37200000002 --repo {AB} --log-failed" in gh.calls

    def test_a_run_dispatched_seconds_before_its_receipt_is_still_tied(self):
        doc = fixture()
        key = runs_key(BP, "self-agent-task.yml")
        runs = json.loads(doc["gh"][key])
        runs.insert(0, {"databaseId": 37100000008, "status": "queued", "conclusion": None,
                        "createdAt": "2026-10-01T00:16:38Z", "event": "repository_dispatch"})
        doc["gh"][key] = json.dumps(runs)
        items, _ctx, _gh = plan(doc)
        assert actions(items, "DRE-9107") == []

    def test_a_run_past_ten_minutes_is_not_the_cards(self):
        # 37100000003 was created fourteen minutes after DRE-9107's newest
        # receipt — another card's run, never this one's.
        items, _ctx, _gh = plan()
        assert "no run after" in actions(items, "DRE-9107")[0].cause

    def test_a_third_redispatch_the_same_pt_day_is_left_alone(self):
        items, _ctx, _gh = plan()
        assert actions(items, "DRE-9108") == []
        (row,) = lefts(items, "DRE-9108")
        assert "hyg-cause-named" in row.why

    def test_the_stop_is_per_pt_day(self):
        doc = fixture()
        card = card_of(doc, "DRE-9108")
        for node in card["comments"]["nodes"]:
            if node["body"].startswith("🧹 hygiene:"):
                node["createdAt"] = "2026-09-29T19:05:00Z"
        items, _ctx, _gh = plan(doc)
        (action,) = actions(items, "DRE-9108")
        assert action.cause == "no run after the re-dispatch at 2026-09-30 17:52 PT"

    def test_one_redispatch_is_not_enough(self):
        doc = fixture()
        nodes = card_of(doc, "DRE-9107")["comments"]["nodes"]
        nodes.remove(next(n for n in reversed(nodes) if n["body"] == REDISPATCH))
        items, _ctx, _gh = plan(doc)
        assert actions(items, "DRE-9107") == [] and lefts(items, "DRE-9107") == []

    def test_a_receipt_inside_the_todo_window_is_too_young(self):
        items, _ctx, _gh = plan(now=datetime(2026, 10, 1, 0, 30, tzinfo=UTC))
        assert actions(items, "DRE-9107") == [] and lefts(items, "DRE-9107") == []

    def test_a_heartbeat_means_a_run_started(self):
        for mark in ("⏳ 1/5 plan", "🧠 model-attempt: claude-opus-5-5"):
            doc = fixture()
            add_comment(card_of(doc, "DRE-9107"), mark, "2026-10-01T00:20:00Z")
            items, _ctx, _gh = plan(doc)
            assert actions(items, "DRE-9107") == [] and lefts(items, "DRE-9107") == [], mark

    def test_a_hand_built_card_is_not_a_build_card(self):
        doc = fixture()
        card_of(doc, "DRE-9107")["labels"]["nodes"].append({"name": "hand-built"})
        items, _ctx, _gh = plan(doc)
        assert actions(items, "DRE-9107") == [] and lefts(items, "DRE-9107") == []

    def test_only_todo_is_read_for_this_rule(self):
        doc = fixture()
        move(doc, "DRE-9107", "In Progress")
        items, _ctx, _gh = plan(doc)
        assert actions(items, "DRE-9107") == [] and lefts(items, "DRE-9107") == []


# --------------------------------------------------------------------------- #
# over the whole fixture                                                       #
# --------------------------------------------------------------------------- #

_COUNT = re.compile(r"\b\d+\s+(?:time|times|attempt|attempts|re-dispatch|re-dispatches|"
                    r"minute|minutes|hour|hours)\b", re.I)
_TIME = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) PT")
_PT = __import__("zoneinfo").ZoneInfo("America/Los_Angeles")


def _pt_moments(doc) -> set:
    """Every moment the fixture holds — merges and comments — as `date HH:MM`."""
    stamps = []
    for answer in doc["gh"].values():
        stamps += re.findall(r'"mergedAt": "([^"]+)"', answer)
    for cards in doc["lanes"].values():
        for card in cards:
            stamps += [n["createdAt"] for n in card["comments"]["nodes"]]
    out = set()
    for s in stamps:
        when = datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(_PT)
        out.add((when.strftime("%Y-%m-%d"), when.strftime("%H:%M")))
    return out


class TestOverTheWholeFixture:
    def test_the_fixture_yields_exactly_the_six_actions_and_seven_left_rows(self):
        items, _ctx, _gh = plan()
        assert sorted((a.target, a.act) for a in actions(items)) == [
            ("DRE-3904", "hygiene-proof-close"),
            ("DRE-9101", "hygiene-card-close"),
            ("DRE-9102", "hygiene-card-close"),
            ("DRE-9104", "hygiene-card-cancel"),
            ("DRE-9107", "hygiene-cause-name"),
            ("DRE-9109", "hygiene-cause-name"),
        ]
        assert sorted(r.target for r in lefts(items)) == [
            "DRE-3503", "DRE-3646", "DRE-5412", "DRE-9103", "DRE-9107", "DRE-9108",
            "DRE-9109"]

    def test_no_cause_carries_a_count_or_the_passs_own_time(self):
        doc = fixture()
        items, _ctx, _gh = plan(doc)
        moments = _pt_moments(doc)
        for action in actions(items):
            assert not _COUNT.search(action.cause), action.cause
            assert PASS_CLOCK not in action.cause, action.cause
            for date, clock in _TIME.findall(action.cause):
                assert (date, clock) in moments, action.cause

    def test_no_state_write_reaches_a_lane_the_agent_never_writes(self):
        items, _ctx, _gh = plan()
        written = {w.lane for a in actions(items) for w in a.writes if w.kind == "linear_state"}
        assert written == {"Done", "Canceled"}
        assert not written & {"In Progress", "Todo", "Planning", "Backlog"}

    def test_a_card_with_children_is_never_closed_or_canceled(self):
        doc = fixture()
        for cards in doc["lanes"].values():
            for card in cards:
                card["children"]["nodes"] = [{"id": "c1", "identifier": "DRE-1",
                                              "createdAt": "2026-09-01T00:00:00Z",
                                              "state": {"name": "Todo"}}]
        items, ctx, _gh = plan(doc)
        assert not [w for a in actions(items) for w in a.writes
                    if w.kind == "linear_state"]
        epic = card_of(doc, "DRE-9101")
        for to in ("Done", "Canceled"):
            with pytest.raises(hygiene.Forbidden):
                hygiene.guard(hygiene.linear_state(epic, to), ctx)

    def test_every_write_passes_the_cores_guard(self):
        items, ctx, _gh = plan()
        for action in actions(items):
            for write in action.writes:
                hygiene.guard(write, ctx)

    def test_every_read_is_answered_from_the_fixture(self):
        doc = fixture()
        _items, _ctx, gh = plan(doc)
        never = {k for k in doc["gh"] if "DRE-5412" in k or "hygiene-proof-2026-10" in k}
        assert set(gh.calls) == set(doc["gh"]) - never
        assert len(gh.calls) == len(set(gh.calls))  # each read once per pass


def run(doc, monkeypatch):
    """One leg through the core, this lane alone, the write seam recorded."""
    sent: list = []
    monkeypatch.setattr(hygiene, "send", lambda write, ctx: sent.append(write))
    monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [need_lane()])
    ctx, _gh = context(doc)
    board_doc = {"taken_at": "2026-10-01T01:40:00Z", "lanes": doc["lanes"]}
    return sent, hygiene.run_leg(board_doc, ctx)


def add_receipts(doc, sent):
    for write in sent:
        if write.kind == "linear_comment":
            add_comment(card_of(doc, write.card), write.body, "2026-10-01T01:40:00Z")


class TestIdempotency:
    def test_a_second_pass_over_an_unchanged_fixture_sends_nothing(self, monkeypatch):
        doc = fixture()
        sent, ledger = run(doc, monkeypatch)
        assert sorted({w.card for w in sent}) == [
            "DRE-3904", "DRE-9101", "DRE-9102", "DRE-9104", "DRE-9107", "DRE-9109"]
        assert all(a["outcome"] == "executed" for a in ledger["actions"])
        add_receipts(doc, sent)
        sent2, ledger2 = run(doc, monkeypatch)
        assert sent2 == []
        assert sorted(r["target"] for r in ledger2["left"]) == \
            sorted(r["target"] for r in ledger["left"])


# --------------------------------------------------------------------------- #
# what it may write, and what it may never carry                               #
# --------------------------------------------------------------------------- #

ALLOWED = {"linear_comment", "linear_state"}
WRITE_CONSTRUCTORS = {"linear_state", "linear_comment", "linear_label", "linear_relation",
                      "gh_dispatch", "gh_rerun", "gh_update_branch", "gh_pr_close",
                      "gh_pr_comment"}


class TestWhatItMayWrite:
    def test_only_comments_and_state_writes_are_returned(self):
        items, _ctx, _gh = plan()
        assert {w.kind for a in actions(items) for w in a.writes} == ALLOWED

    def test_the_module_calls_no_other_constructor(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        used = {n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "hygiene" and n.attr in WRITE_CONSTRUCTORS}
        assert used == ALLOWED

    def test_the_cores_static_scan_passes_over_it(self):
        spec = importlib.util.spec_from_file_location(
            "test_hygiene_core", ROOT / "tests" / "test_hygiene.py")
        core_tests = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core_tests)
        assert MODULE_PATH.exists()
        assert core_tests.scan(MODULE_PATH) == []


class TestAReadThatFails:
    def test_a_failed_read_skips_that_card_and_no_other(self, capsys):
        doc = fixture()
        doc["gh"][lookup_key(BP, "DRE-9101")] = RuntimeError("gh exited 1: HTTP 502")
        items, _ctx, _gh = plan(doc)
        assert actions(items, "DRE-9101") == []
        assert [a.act for a in actions(items, "DRE-9102")] == ["hygiene-card-close"]
        assert "DRE-9101" in capsys.readouterr().err

    def test_a_read_the_core_refuses_is_never_swallowed(self):
        doc = fixture()
        doc["gh"][lookup_key(BP, "DRE-9101")] = hygiene.Forbidden("refused")
        with pytest.raises(hygiene.Forbidden):
            plan(doc)


def test_the_fixture_clock_is_the_pass_clock():
    # The cause-time assertions above read 18:40 PT as the pass's own clock.
    assert hygiene.pt(NOW) == f"{PASS_CLOCK} PT"
