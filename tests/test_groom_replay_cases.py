"""RED-first: every removal a person records with a reason becomes a replay
case, kept in the repo (DRE-7971).

The memory (DRE-7824, DRE-7826) keeps a removed card off later proposals, but
the verify step never sees what it got wrong. `scripts/groom_replay_cases.py`
reads every decision ever recorded about a card on a proposal, uncollapsed
(`groom_memory.all_decisions`), and the proposal each was made on, and writes
one case per proposal and card a person removed it from — then the two
stored proposals the replay runs them through:

  * `replay-set.json`, every case card, replayed on `main` each Sunday;
  * `gate-set.json`, only the cards of cases that pass on `main`, at most
    `GATE_MAX_CARDS`, replayed on a pull request by the gate.

The failures this file holds shut: a removal with no reason becoming a case;
a Cancel-list `cancel` (the person agreed) becoming one; an older removal
lost because a later decision replaced it; a case's `main` block rewritten
by a rebuild; a stored set `groom_replay.base` refuses, or one whose window is
not exactly the case cards.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_replay_cases.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_memory  # noqa: E402
import groom_replay  # noqa: E402
import groom_replay_cases as grc  # noqa: E402
import groom_verify_agent  # noqa: E402
import groomer  # noqa: E402

P = "aaaa11112222"
P2 = "bbbb33334444"
STANDING = "DRE-4541"
REASONS = [{"key": "already-done", "label": "Already done"},
           {"key": "waits-on-other-work", "label": "Not ready: waits on other work"}]
SEED_TITLE = ("bureau-pipeline: groom_replay.py turns a stored groom proposal "
              "into a run's input and reports where each card ended")
SEED = {"case": "seed-DRE-7239", "card": "DRE-7239", "proposal_id": None,
        "standing_card": None, "list": "planning", "decision": None,
        "reason": None,
        "reason_label": ("seed: known-good — the card's work is merged, so the "
                         "verify step takes it off the Planning list"),
        "recorded_at": "2026-10-25T00:00:00Z", "source": "seed",
        "expect": "off-planning", "title": SEED_TITLE, "repo": "bureau-pipeline",
        "main": {"ended": None, "at": None, "passes": None}}
CASE_KEYS = list(SEED)

TAGS = {"excluded": groomer.EXCLUDE_TAG, "held": groomer.HOLD_TAG,
        "added": groomer.ADD_TAG}


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def proposal_comment(pid, planning=(), cancel=(), *, at="2026-10-20T13:00:00Z"):
    """A posted proposal as `parse_proposal_comment` reads it back: `planning`
    and `cancel` are `(card, repo, title)`."""
    lines = [f"{groomer.MARK} {groomer.PROPOSAL_TAG}: {pid}", "",
             f"# Groom proposal `{pid}` — cycle 15", "",
             "## The batch, in order", "",
             "| # | Card | Pri | Repo | Epic | Title | Why |",
             "| -- | -- | -- | -- | -- | -- | -- |"]
    for n, (card, repo, title) in enumerate(planning, 1):
        lines.append(f"| {n} | {card} | High | {repo} | — | {title} | the rules |")
    lines += ["", groomer.CANCEL_HEADING, "", groomer.CANCEL_COLUMNS,
              "| -- | -- | -- | -- | -- | -- | -- |"]
    for n, (card, repo, title) in enumerate(cancel, 1):
        escaped = title.replace("|", "\\|")
        lines.append(f"| {n} | {card} | High | {repo} | — | {escaped} | "
                     f"done elsewhere |")
    return {"body": "\n".join(lines) + "\n", "authored_by_pipeline": True,
            "created_at": at}


def said(kind, card, tail=None, *, at="2026-10-21T16:00:00Z", pid=P):
    """A person's per-card marker on a thread, as `comment_records` gives it."""
    rest = f" — {tail}" if tail else ""
    return {"body": f"{groomer.MARK} {TAGS[kind]}: {pid} {card}{rest}",
            "authored_by_pipeline": False, "created_at": at}


def row(card, decision="not-doing", *, at="2026-10-21T15:00:00Z", pid=P,
        reason=None, comment=None, ident=1, standing=STANDING):
    return {"id": ident, "recorded_at": at, "standing_card": standing,
            "proposal_id": pid, "card": card, "decision": decision,
            "reason": reason, "hold_until_card": None, "hold_until_date": None,
            "actor_name": "Sid", "comment": comment}


def inputs(threads, rows=(), *, reasons=REASONS, door_problem=None):
    doc = {"threads": threads}
    if door_problem is None:
        doc["removals"] = {"reasons": list(reasons), "decisions": list(rows)}
    else:
        doc["door_problem"] = door_problem
    return doc


class Files:
    """One build's three files in a temporary directory, the shipped ones
    copied in first."""

    def __init__(self, tmp_path: Path, cases=None):
        self.dir = tmp_path
        self.cases = tmp_path / "cases.json"
        self.set = tmp_path / "replay-set.json"
        self.gate = tmp_path / "gate-set.json"
        if cases is None:
            self.cases.write_text((ROOT / grc.CASES_PATH).read_text())
        else:
            self.cases.write_text(json.dumps(cases))

    def build(self, doc, capsys=None) -> tuple[int, str]:
        source = self.dir / "from.json"
        source.write_text(json.dumps(doc))
        rc = grc.main(["build", "--from", str(source), "--cases", str(self.cases),
                       "--set", str(self.set), "--gate-set", str(self.gate)])
        out = capsys.readouterr().out if capsys else ""
        return rc, out

    def read(self):
        return json.loads(self.cases.read_text())

    def by_id(self):
        return {c["case"]: c for c in self.read()["cases"]}


A_TITLE = "agent-bureau: the console shows each card's age"
B_TITLE = "portico: the release | train names its lane"


def the_morning():
    """Proposal P on DRE-4541: four Planning cards, two Cancel cards."""
    return proposal_comment(
        P,
        planning=[("DRE-201", "agent-bureau", A_TITLE),
                  ("DRE-204", "agent-bureau", "agent-bureau: four"),
                  ("DRE-206", "portico", "portico: six"),
                  ("DRE-207", "portico", "portico: seven")],
        cancel=[("DRE-202", "portico", B_TITLE),
                ("DRE-203", "bureau-pipeline", "bureau-pipeline: three")])


# --------------------------------------------------------------------------
# the contract's constants
# --------------------------------------------------------------------------
def test_the_constants_are_the_contract():
    assert grc.SCHEMA == "groom-replay-cases/1"
    assert grc.CASES_PATH == "tests/fixtures/groom-replay-cases/cases.json"
    assert grc.SET_PATH == "tests/fixtures/groom-replay-cases/replay-set.json"
    assert grc.GATE_SET_PATH == "tests/fixtures/groom-replay-cases/gate-set.json"
    assert grc.GATE_MAX_CARDS == 50
    assert grc.SEED_SOURCE == "seed"
    assert grc.SEEDS == ("DRE-7239",)
    assert grc.EXPECTS == ("off-planning", "off-cancel")
    assert grc.REMOVALS == {"planning": ("not-doing", "hold", "cancel"),
                            "cancel": ("not-doing", "hold")}
    assert grc.BUILD_TAG == "groom-replay-cases:"


# --------------------------------------------------------------------------
# the build over one morning
# --------------------------------------------------------------------------
def test_one_morning_gives_exactly_the_two_removals_with_reasons(tmp_path, capsys):
    rows = [row("DRE-201", reason="already-done", ident=1),
            row("DRE-203", "cancel", reason="already-done", ident=2),
            row("DRE-207", "put-back", comment="ready now", ident=3),
            row("DRE-206", ident=4),
            row("DRE-208", reason="already-done", pid="ffff00000000", ident=5)]
    thread = [the_morning(),
              said("held", "DRE-202", "keep it, the console still reads it"),
              said("excluded", "DRE-204", "move to Cancel")]
    files = Files(tmp_path)
    rc, out = files.build(inputs({STANDING: thread}, rows), capsys)
    assert rc == 0, out
    got = files.by_id()
    assert set(got) == {"seed-DRE-7239", f"{P}-DRE-201", f"{P}-DRE-202"}

    planning = got[f"{P}-DRE-201"]
    assert planning["expect"] == "off-planning"
    assert planning["list"] == "planning"
    assert planning["decision"] == "not-doing"
    assert planning["reason"] == "already-done"
    assert planning["reason_label"] == "Already done"
    assert planning["title"] == A_TITLE
    assert planning["repo"] == "agent-bureau"
    assert planning["source"] == "record"
    assert planning["standing_card"] == STANDING
    assert planning["proposal_id"] == P

    kept = got[f"{P}-DRE-202"]
    assert kept["expect"] == "off-cancel"
    assert kept["list"] == "cancel"
    assert kept["decision"] == "hold"
    assert kept["reason"] is None
    assert kept["reason_label"] == "keep it, the console still reads it"
    assert kept["title"] == B_TITLE
    assert kept["repo"] == "portico"
    assert kept["source"] in groom_memory.SOURCES

    for case in got.values():
        assert list(case) == CASE_KEYS
        assert case["main"] == {"ended": None, "at": None, "passes": None}
    assert out.strip() == (
        "groom-replay-cases: build 3 cases (2 new) — 2 no reason — gate 0 cards "
        "(0 over the ceiling) — source=record")


@pytest.mark.parametrize("on_list,kind,expect", [
    ("planning", "not-doing", "off-planning"),
    ("planning", "cancel", "off-planning"),
    ("planning", "hold", "off-planning"),
    ("planning", "put-back", None),
    ("cancel", "not-doing", "off-cancel"),
    ("cancel", "cancel", None),
    ("cancel", "hold", "off-cancel"),
    ("cancel", "put-back", None),
])
def test_the_pairing_table_row_by_row(tmp_path, capsys, on_list, kind, expect):
    card = ("DRE-301", "agent-bureau", "agent-bureau: the card")
    comment = proposal_comment(P, planning=[card] if on_list == "planning" else [],
                               cancel=[card] if on_list == "cancel" else [])
    files = Files(tmp_path)
    rc, out = files.build(inputs({STANDING: [comment]},
                                 [row("DRE-301", kind, reason="already-done")]),
                          capsys)
    assert rc == 0, out
    made = [c for c in files.read()["cases"] if c["card"] == "DRE-301"]
    if expect is None:
        assert made == []
    else:
        assert [c["expect"] for c in made] == [expect]
        assert made[0]["list"] == on_list
        assert made[0]["decision"] == kind
    assert " — 0 no reason — " in out


def test_every_kind_in_the_vocabulary_is_in_the_pairing_table():
    """The parametrized table above names each list and each kind once."""
    kinds = set(groom_memory.DECISIONS)
    assert kinds == {"not-doing", "cancel", "hold", "put-back"}
    for removals in grc.REMOVALS.values():
        assert set(removals) <= kinds
    assert set(grc.REMOVALS) == {"planning", "cancel"}


def test_a_card_removed_on_two_proposals_and_put_back_is_two_cases(tmp_path, capsys):
    card = ("DRE-401", "portico", "portico: the card")
    thread = [proposal_comment(P, planning=[card], at="2026-10-01T13:00:00Z"),
              said("excluded", "DRE-401", "Already done", at="2026-10-01T16:00:00Z"),
              proposal_comment(P2, planning=[card], at="2026-10-08T13:00:00Z"),
              said("held", "DRE-401", "after the release", pid=P2,
                   at="2026-10-08T16:00:00Z"),
              said("added", "DRE-401", "ready now", pid=P2,
                   at="2026-10-09T16:00:00Z")]
    files = Files(tmp_path)
    rc, out = files.build(inputs({STANDING: thread}), capsys)
    assert rc == 0, out
    got = files.by_id()
    assert f"{P}-DRE-401" in got and f"{P2}-DRE-401" in got
    assert got[f"{P}-DRE-401"]["decision"] == "not-doing"
    assert got[f"{P}-DRE-401"]["reason"] == "already-done"
    assert got[f"{P2}-DRE-401"]["decision"] == "hold"
    cards = [r["identifier"]
             for r in grc.replay_set(files.read()["cases"])["outcomes"]["now"]]
    assert cards.count("DRE-401") == 1


def test_two_removals_on_one_proposal_keep_the_newer(tmp_path, capsys):
    card = ("DRE-402", "portico", "portico: the card")
    thread = [proposal_comment(P, planning=[card]),
              said("excluded", "DRE-402", "first words", at="2026-10-21T16:00:00Z"),
              said("held", "DRE-402", "second words", at="2026-10-21T17:00:00Z")]
    files = Files(tmp_path)
    files.build(inputs({STANDING: thread}), capsys)
    case = files.by_id()[f"{P}-DRE-402"]
    assert (case["decision"], case["reason_label"]) == ("hold", "second words")


def test_a_row_and_its_mirrored_marker_make_one_case_with_the_row_reason(tmp_path, capsys):
    thread = [the_morning(),
              said("excluded", "DRE-201", "Already done", at="2026-10-21T15:00:01Z")]
    files = Files(tmp_path)
    rc, out = files.build(inputs({STANDING: thread},
                                 [row("DRE-201", reason="already-done")]), capsys)
    assert rc == 0, out
    made = [c for c in files.read()["cases"] if c["card"] == "DRE-201"]
    assert len(made) == 1
    assert made[0]["source"] == "record"
    assert made[0]["reason"] == "already-done"
    assert "(1 new)" in out


def test_the_build_is_byte_for_byte_the_same_run_twice(tmp_path, capsys):
    rows = [row("DRE-201", reason="already-done")]
    thread = [the_morning(), said("held", "DRE-202", "keep it")]
    files = Files(tmp_path)
    files.build(inputs({STANDING: thread}, rows), capsys)
    first = [p.read_bytes() for p in (files.cases, files.set, files.gate)]
    rc, out = files.build(inputs({STANDING: thread}, rows), capsys)
    assert rc == 0
    assert [p.read_bytes() for p in (files.cases, files.set, files.gate)] == first
    assert "(0 new)" in out


def test_a_case_main_block_survives_a_rebuild(tmp_path, capsys):
    rows = [row("DRE-201", reason="already-done")]
    thread = [the_morning()]
    files = Files(tmp_path)
    files.build(inputs({STANDING: thread}, rows), capsys)
    doc = files.read()
    main = {"ended": "cancel", "at": "2026-10-26T12:00:00Z", "passes": True}
    for case in doc["cases"]:
        case["main"] = dict(main)
    files.cases.write_text(json.dumps(doc))
    files.build(inputs({STANDING: thread}, rows), capsys)
    for case in files.read()["cases"]:
        assert case["main"] == main
    # …and a case whose decision the input no longer holds is never dropped.
    files.build(inputs({STANDING: thread}), capsys)
    assert set(files.by_id()) == {"seed-DRE-7239", f"{P}-DRE-201"}


def test_cases_are_sorted_by_recorded_at_then_card(tmp_path, capsys):
    thread = [the_morning(),
              said("held", "DRE-202", "keep it", at="2026-10-21T16:00:00Z"),
              said("excluded", "DRE-206", "not now", at="2026-10-21T16:00:00Z"),
              said("excluded", "DRE-201", "not now", at="2026-10-21T17:00:00Z")]
    files = Files(tmp_path)
    files.build(inputs({STANDING: thread}), capsys)
    order = [c["card"] for c in files.read()["cases"]]
    assert order == ["DRE-202", "DRE-206", "DRE-201", "DRE-7239"]


def test_a_door_that_cannot_be_read_takes_the_cases_off_the_markers(tmp_path, capsys):
    thread = [the_morning(), said("held", "DRE-202", "keep it")]
    files = Files(tmp_path)
    rc, out = files.build(inputs({STANDING: thread},
                                 door_problem="unavailable: BUREAU_READ_URL is not set"),
                          capsys)
    assert rc == 0, out
    case = files.by_id()[f"{P}-DRE-202"]
    assert case["source"] == "fallback"
    assert out.strip().endswith("source=fallback")


def test_the_live_read_goes_through_the_door_and_every_standing_thread(monkeypatch):
    """Never through `groomer.read_memory`: its answer is collapsed per card."""
    reads = []

    class Lops:
        def comment_records(self, identifier, *, whole_thread=False):
            reads.append((identifier, whole_thread))
            return [the_morning()] if identifier == STANDING else []

    monkeypatch.setattr(grc.bureau_read, "groom_removals",
                        lambda since=None: {"reasons": REASONS,
                                            "decisions": [row("DRE-201", reason="already-done")]})
    monkeypatch.setattr(grc.groomer, "read_memory",
                        lambda *a, **k: pytest.fail("read through read_memory"))
    got = grc.read_live(Lops(), "DRE-9999")
    assert [c for c, _ in reads] == list(groom_memory.standing_cards("DRE-9999"))
    assert all(whole for _, whole in reads)
    assert got["door_problem"] is None
    assert got["rows"][0]["card"] == "DRE-201"

    def down(since=None):
        raise grc.bureau_read.ReadUnknown("unavailable", "no url")
    monkeypatch.setattr(grc.bureau_read, "groom_removals", down)
    got = grc.read_live(Lops(), None)
    assert got["rows"] == [] and got["door_problem"] == "unavailable: no url"


# --------------------------------------------------------------------------
# passes
# --------------------------------------------------------------------------
LISTS = [groom_replay.PLANNING, groom_replay.CANCEL, groom_replay.REWRITE,
         groom_replay.NOT_VERIFIED, groom_replay.EXCLUDED, groom_replay.NOT_NOW,
         groom_replay.UNPLACED, None]


@pytest.mark.parametrize("ended", LISTS)
@pytest.mark.parametrize("expect", ["off-planning", "off-cancel"])
def test_passes_for_every_expect_and_every_list(expect, ended):
    case = {"expect": expect}
    got = grc.passes(case, ended, judged=True)
    if ended in (None, groom_replay.EXCLUDED, groom_replay.UNPLACED):
        assert got is None
    elif expect == "off-planning":
        assert got is (ended != groom_replay.PLANNING)
    else:
        assert got is (ended != groom_replay.CANCEL)
    assert grc.passes(case, ended, judged=False) is None


# --------------------------------------------------------------------------
# the stored sets
# --------------------------------------------------------------------------
def case(card, *, at, passes=None, repo="portico", title=None, pid=P):
    return {"case": f"{pid}-{card}", "card": card, "proposal_id": pid,
            "standing_card": STANDING, "list": "planning",
            "decision": "not-doing", "reason": None, "reason_label": "words",
            "recorded_at": at, "source": "record", "expect": "off-planning",
            "title": title or f"{repo}: {card}", "repo": repo,
            "main": {"ended": None, "at": None, "passes": passes}}


THREE = [case("DRE-501", at="2026-10-01T00:00:00Z", repo="agent-bureau"),
         case("DRE-502", at="2026-10-02T00:00:00Z", repo="portico"),
         case("DRE-503", at="2026-10-03T00:00:00Z", repo="bureau-pipeline")]


def test_replay_set_goes_through_base_and_its_window_is_the_case_cards(tmp_path, capsys):
    stored = tmp_path / "tests" / "fixtures" / "replay-set.json"
    stored.parent.mkdir(parents=True)
    stored.write_text(json.dumps(grc.replay_set(THREE)))
    out = tmp_path / "proposal.json"
    rc = groom_replay.main(["base", "--stored", "tests/fixtures/replay-set.json",
                            "--out", str(out)], root=tmp_path)
    assert rc == 0, capsys.readouterr()
    assert "3 cards in the verify window" in capsys.readouterr().out
    proposal = json.loads(out.read_text())
    assert groom_verify_agent.window(proposal) == [
        ("DRE-501", "planning"), ("DRE-502", "planning"), ("DRE-503", "planning")]
    for rows in (proposal["outcomes"]["now"], proposal["sequence"]):
        assert [(r["identifier"], r["title"], r["repo"], r["position"]) for r in rows] == [
            (c["card"], c["title"], c["repo"], n) for n, c in enumerate(THREE, 1)]
    assert all(rows == [] for name, rows in proposal["outcomes"].items()
               if name != "now")


def test_replay_set_survives_the_workflow_steps_after_the_verify_legs(tmp_path):
    """No verdict at all, then the page and the report — what the groomer
    workflow runs over a replay — never fail on the stored set's shape."""
    proposal = groom_replay.base(grc.replay_set(THREE))
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(proposal))
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    verified = tmp_path / "proposal-verified.json"
    rc = groom_verify_agent.main(["apply", "--proposal", str(path), "--verdicts",
                                  str(verdicts), "--out", str(verified)])
    assert rc in (0, None)
    done = json.loads(verified.read_text())
    assert groomer.render_proposal(done)
    lines = groom_replay.report(done)
    assert lines[-1].startswith("groom-replay: 3 cards — ")


def test_a_card_on_two_cases_is_one_row_in_the_replay_set():
    cases = THREE + [case("DRE-501", at="2026-10-04T00:00:00Z", pid=P2)]
    rows = grc.replay_set(cases)["outcomes"]["now"]
    assert [r["identifier"] for r in rows] == ["DRE-501", "DRE-502", "DRE-503"]


def test_the_gate_set_is_the_distinct_passing_cards_newest_first(tmp_path, capsys,
                                                                 monkeypatch):
    cases = [case("DRE-601", at="2026-10-01T00:00:00Z", passes=True),
             case("DRE-602", at="2026-10-02T00:00:00Z", passes=False),
             case("DRE-603", at="2026-10-03T00:00:00Z", passes=None),
             case("DRE-604", at="2026-10-04T00:00:00Z", passes=True),
             case("DRE-601", at="2026-10-05T00:00:00Z", passes=True, pid=P2)]
    rows = grc.gate_set(cases)["outcomes"]["now"]
    assert [r["identifier"] for r in rows] == ["DRE-601", "DRE-604"]
    assert [r["position"] for r in rows] == [1, 2]
    one = grc.gate_set(cases, limit=1)["outcomes"]["now"]
    assert [r["identifier"] for r in one] == ["DRE-601"]

    files = Files(tmp_path, cases={"schema": grc.SCHEMA, "built_at": None,
                                   "cases": [SEED] + cases})
    monkeypatch.setattr(grc, "GATE_MAX_CARDS", 1)
    rc, out = files.build(inputs({}), capsys)
    assert rc == 0, out
    assert " — gate 1 cards (1 over the ceiling) — " in out
    gate = json.loads(files.gate.read_text())
    assert [r["identifier"] for r in gate["outcomes"]["now"]] == ["DRE-601"]


def test_an_empty_gate_set_says_zero_cards_so_a_workflow_can_skip_it(tmp_path, capsys):
    stored = tmp_path / "gate.json"
    stored.write_text(json.dumps(grc.gate_set([SEED])))
    rc = groom_replay.main(["base", "--stored", "gate.json",
                            "--out", str(tmp_path / "p.json")], root=tmp_path)
    assert rc == 0
    assert "— 0 cards in the verify window" in capsys.readouterr().out


# --------------------------------------------------------------------------
# the shipped files and the seed
# --------------------------------------------------------------------------
def test_the_shipped_cases_hold_the_seed_and_nothing_else():
    doc = json.loads((ROOT / grc.CASES_PATH).read_text())
    assert doc == {"schema": "groom-replay-cases/1", "built_at": None,
                   "cases": [SEED]}
    assert list(doc["cases"][0]) == CASE_KEYS


def test_the_shipped_sets_are_built_from_the_shipped_cases():
    cases = json.loads((ROOT / grc.CASES_PATH).read_text())["cases"]
    assert json.loads((ROOT / grc.SET_PATH).read_text()) == grc.replay_set(cases)
    gate = json.loads((ROOT / grc.GATE_SET_PATH).read_text())
    assert gate == grc.gate_set(cases)
    assert gate["outcomes"]["now"] == [] and gate["sequence"] == []
    assert [r["identifier"] for r in
            json.loads((ROOT / grc.SET_PATH).read_text())["outcomes"]["now"]] == ["DRE-7239"]


def test_the_shipped_sets_go_through_base_in_the_checkout(tmp_path):
    for stored, n in ((grc.SET_PATH, 1), (grc.GATE_SET_PATH, 0)):
        out = tmp_path / "p.json"
        assert groom_replay.main(["base", "--stored", stored, "--out", str(out)]) == 0
        assert len(groom_verify_agent.window(json.loads(out.read_text()))) == n


def test_the_shipped_files_are_what_a_build_writes(tmp_path, capsys):
    files = Files(tmp_path)
    rc, out = files.build(inputs({}), capsys)
    assert rc == 0, out
    for mine, shipped in ((files.cases, grc.CASES_PATH), (files.set, grc.SET_PATH),
                          (files.gate, grc.GATE_SET_PATH)):
        assert mine.read_bytes() == (ROOT / shipped).read_bytes(), shipped


def test_a_build_with_no_decisions_keeps_the_seed_unchanged(tmp_path, capsys):
    files = Files(tmp_path)
    rc, out = files.build(inputs({}), capsys)
    assert rc == 0, out
    assert files.read()["cases"] == [SEED]
    assert out.strip() == (
        "groom-replay-cases: build 1 cases (0 new) — 0 no reason — gate 0 cards "
        "(0 over the ceiling) — source=record")


def test_a_seed_whose_main_block_was_set_keeps_it(tmp_path, capsys):
    seeded = dict(SEED, main={"ended": "cancel", "at": "2026-10-26T12:00:00Z",
                              "passes": True})
    files = Files(tmp_path, cases={"schema": grc.SCHEMA, "built_at": None,
                                   "cases": [seeded]})
    files.build(inputs({}), capsys)
    assert files.read()["cases"] == [seeded]
    gate = json.loads(files.gate.read_text())
    assert [r["identifier"] for r in gate["outcomes"]["now"]] == ["DRE-7239"]


def test_a_missing_seed_is_put_back(tmp_path, capsys):
    files = Files(tmp_path, cases={"schema": grc.SCHEMA, "built_at": None,
                                   "cases": []})
    files.build(inputs({}), capsys)
    assert files.read()["cases"] == [SEED]


def test_the_seed_card_is_the_one_the_constant_names():
    assert [SEED["card"]] == list(grc.SEEDS)
    assert grc.SEED_CASES == (SEED,)
