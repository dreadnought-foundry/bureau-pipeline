"""RED-first tests: which Green Light rows are the Operator Agent's (DRE-9048).

`Green Light` is the CEO's decision queue. Some rows in it are not decisions —
a run that stopped, a question the facts answer — and those are the agent's.
`scripts/operator_green_light.py` is the one pure reading that rules out, before
any model reads a card, every row that is never the agent's: one the agent
already read, a plan waiting for the CEO's approval, a growth question, a
signed-answer park, a row the hygiene agent acts on, a notice, and a row already
screened as a business choice.

The rows are `tests/fixtures/operator-green-light-cards.json`. Every row but the
live ones is built from a record of the decision card's fixture
(`tests/fixtures/operator-decide-green-light-cards.json`) and is never copied
again: a record names its `base`, and its `card` holds only what the builder
wrote. Rule 5's rows are the nine cards of
`tests/fixtures/hygiene-green-light-2026-09-30.json`, read from that file.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_green_light.py -v
"""
from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
import os
import re
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import console_receipt  # noqa: E402
import console_receipt_vectors as V  # noqa: E402
import hygiene  # noqa: E402
import hygiene_green_light  # noqa: E402
import linear_ops  # noqa: E402
import operator_decide  # noqa: E402
import operator_rules  # noqa: E402
import spoken_thread  # noqa: E402

MODULE = ROOT / "scripts" / "operator_green_light.py"
FIXTURE = ROOT / "tests" / "fixtures" / "operator-green-light-cards.json"
DECIDE = ROOT / "tests" / "fixtures" / "operator-decide-green-light-cards.json"
HYGIENE = ROOT / "tests" / "fixtures" / "hygiene-green-light-2026-09-30.json"
REPLAY = ROOT / "tests" / "fixtures" / "operator-agent-board-2026-11-01.json"
RULES_TEST = ROOT / "tests" / "test_operator_rules.py"

OPENSSL = V.capable_openssl()
PIPELINE_USER = "user-pipeline"
LANE = "Green Light"
#: 2026-09-30 21:05 UTC — the hygiene fixture's own pass.
HYGIENE_NOW = datetime(2026, 9, 30, 21, 5, tzinfo=UTC)

RECORD_KEYS = {"serves", "source", "copied", "cut_after", "added", "removed", "withheld",
               "base", "card"}

#: Each record of the fixture: (taken, the constant its `why` is).
EXPECTED = {
    "r1-ceo": (False, "SENT_WHY"),
    "r1-keep": (False, "KEPT_WHY"),
    "r1-keep-newer": (True, "TAKEN_WHY"),
    "r1-rekept": (False, "KEPT_WHY"),
    "r2-plan": (False, "PLAN_WHY"),
    "r2-queued": (False, "PLAN_WHY"),
    "r3-growth": (False, "GROWTH_WHY"),
    "r4-proof": (False, "SIGNED_WHY"),
    "r4-press": (False, "SIGNED_WHY"),
    "r4-signed": (False, "SIGNED_WHY"),
    "r6-report": (False, "NOTICE_WHY"),
    "r6-deferred": (False, "NOTICE_WHY"),
    "r6-owner": (False, "NOTICE_WHY"),
    "r6-design": (False, "NOTICE_WHY"),
    "r7-screened-2498": (False, "CHOICE_WHY"),
    "r7-screened-6589": (False, "CHOICE_WHY"),
    "taken-limit": (True, "TAKEN_WHY"),
}
#: The rows a console signature decides: verified only with an Ed25519 openssl.
SIGNED_ROWS = {"r4-signed"}

WHYS = ("SENT_WHY", "KEPT_WHY", "PLAN_WHY", "GROWTH_WHY", "SIGNED_WHY", "HYGIENE_WHY",
        "NOTICE_WHY", "CHOICE_WHY", "TAKEN_WHY", "NO_VIEWER")


def load():
    if not MODULE.exists():
        return None
    import operator_green_light

    return operator_green_light


ogl = load()


@pytest.fixture(autouse=True)
def _console_key(monkeypatch):
    """The console's key is the TEST key, read from the vectors and never
    fetched, so no row's reading reaches the network for it."""
    if OPENSSL is not None:
        monkeypatch.setenv("OPENSSL_BIN", OPENSSL)
        monkeypatch.setattr(console_receipt, "_OPENSSL", None, raising=False)
    monkeypatch.setattr(spoken_thread, "_VERIFIER", console_receipt.Verifier(
        key_loader=lambda: console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)))


def _module():
    assert ogl is not None, f"{MODULE.name} does not exist yet"
    return ogl


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _records() -> dict:
    return _json(FIXTURE)


def _merge(card: dict, delta: dict) -> dict:
    """`delta` over `card`: a title replaces the base's, labels are added to
    the base's, and a comment replaces the base's comment of the same
    `createdAt` or is added beside them — newest first, as the board reads."""
    card = copy.deepcopy(card)
    if "title" in delta:
        card["title"] = delta["title"]
    for label in (delta.get("labels") or {}).get("nodes") or []:
        card["labels"]["nodes"].append(copy.deepcopy(label))
    added = (delta.get("comments") or {}).get("nodes") or []
    if added:
        at = {node["createdAt"]: node for node in card["comments"]["nodes"]}
        at.update({node["createdAt"]: copy.deepcopy(node) for node in added})
        card["comments"]["nodes"] = sorted(at.values(), key=lambda n: n["createdAt"],
                                           reverse=True)
    return card


def build(record_id: str) -> dict:
    """The record's card in the board read's shape: its base, built the same
    way, with the record's own fields over it."""
    record = _records()[record_id]
    base = record["base"]
    if base["file"] == "tests/fixtures/operator-decide-green-light-cards.json":
        card = _json(DECIDE)[base["record"]]["card"]
    else:
        assert base["file"] == "tests/fixtures/operator-green-light-cards.json", record_id
        card = build(base["record"])
    return _merge(card, record["card"])


def decision_card(record_id: str) -> dict:
    return copy.deepcopy(_json(DECIDE)[record_id]["card"])


def thread(card: dict) -> list:
    """The card's comments as `linear_ops.comment_records(whole_thread=True)`
    rows, oldest to newest, read with the pipeline's own key."""
    return [{"body": node.get("body") or "",
             "authored_by_pipeline": ((node.get("user") or {}).get("id")) == PIPELINE_USER,
             "created_at": node.get("createdAt")}
            for node in linear_ops.window_nodes(card.get("comments"))]


def read(card: dict, viewer: str | None = PIPELINE_USER):
    return _module().reading(card, thread(card), viewer)


def expected(record_id: str):
    taken, why = EXPECTED[record_id]
    return _module().Reading(taken=taken, why=getattr(_module(), why))


def _needs_openssl(record_id: str) -> None:
    if record_id in SIGNED_ROWS and OPENSSL is None:
        if os.environ.get("CI"):
            pytest.fail("no Ed25519-capable openssl on a CI runner")
        pytest.skip("no Ed25519-capable openssl on this machine")


def push(card: dict, body: str, at: str, author: str | None = PIPELINE_USER) -> dict:
    card = copy.deepcopy(card)
    card["comments"]["nodes"].insert(0, {"body": body, "createdAt": at,
                                         "user": {"id": author} if author else None})
    return card


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


def test_reading_is_a_frozen_pair_of_taken_and_why():
    module = _module()
    assert [f.name for f in dataclasses.fields(module.Reading)] == ["taken", "why"]
    reading = module.Reading(taken=True, why=module.TAKEN_WHY)
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.taken = False


def test_the_constants_the_contract_names():
    module = _module()
    assert module.KEPT_WHY == "the agent read it and left it with the CEO"
    assert module.TAKEN_WHY == "not a decision this agent is barred from"
    assert module.NO_VIEWER == ("the pipeline's Linear user could not be read — no receipt "
                                "can be told from a stranger's")
    whys = [getattr(module, name) for name in WHYS]
    assert all(isinstance(w, str) and w for w in whys)
    assert len(set(whys)) == len(whys), "every rule has its own why"


def test_no_viewer_takes_no_row():
    module = _module()
    for record_id in list(EXPECTED) + ["gl-stopped-run", "gl-answerable"]:
        card = build(record_id) if record_id in EXPECTED else decision_card(record_id)
        for viewer in (None, ""):
            assert read(card, viewer) == module.Reading(taken=False, why=module.NO_VIEWER)


# --------------------------------------------------------------------------- #
# one test per row                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("record_id", list(EXPECTED))
def test_each_fixture_row_reads_as_its_rule_says(record_id):
    _needs_openssl(record_id)
    assert read(build(record_id)) == expected(record_id)


def test_the_decision_cards_two_rows_are_taken():
    module = _module()
    for record_id in ("gl-stopped-run", "gl-answerable"):
        assert read(decision_card(record_id)) == module.Reading(taken=True,
                                                                why=module.TAKEN_WHY)


def _kept_act_card() -> dict:
    """DRE-8173's committed record, read through `operator_rules.fixture_card`,
    in `Green Light`, in the board read's shape."""
    record = next(r for r in _json(REPLAY) if r["card"] == "DRE-8173")
    row = operator_rules.fixture_card(record)
    return {
        "identifier": row["card"], "title": row["title"], "description": row["description"],
        "state": {"name": LANE},
        "labels": {"nodes": [{"name": label} for label in row["labels"]]},
        "comments": {"nodes": [{"body": c["body"], "createdAt": c["created_at"],
                                "user": {"id": "user-person"}}
                               for c in reversed(row["comments"])]},
    }


def test_dre_8173s_kept_act_in_green_light_is_not_taken_by_rule_7():
    module = _module()
    card = _kept_act_card()
    match = operator_rules.rule_outcome(card["title"], card["description"])
    assert match is not None and match.outcome == operator_rules.CEO
    assert read(card) == module.Reading(taken=False, why=module.CHOICE_WHY)


# --------------------------------------------------------------------------- #
# rule 1 — a row the agent already read                                        #
# --------------------------------------------------------------------------- #


def test_the_rule_1_receipts_read_back_as_the_fixture_says():
    base = decision_card("gl-stopped-run")
    fingerprint = operator_decide.fingerprint(base)
    ceo = operator_decide.newest_receipt(thread(build("r1-ceo")))
    decision = operator_decide.decision_from_receipt(
        {}, operator_decide._without_trailer(ceo["body"]))
    assert decision.outcome == operator_decide.CEO and decision.fingerprint == fingerprint
    for record_id in ("r1-keep", "r1-rekept"):
        assert operator_decide.keep_stands(thread(build(record_id)), fingerprint), record_id
    assert not operator_decide.keep_stands(thread(build("r1-keep-newer")), fingerprint)


def test_a_ceo_receipt_rules_the_row_out_whatever_its_fingerprint():
    module = _module()
    card = build("r1-ceo")
    card["description"] += "\n\nA line added after the receipt."
    assert operator_decide.fingerprint(card) != operator_decide.fingerprint(build("r1-ceo"))
    assert read(card) == module.Reading(taken=False, why=module.SENT_WHY)


def test_a_keep_over_a_changed_description_rules_nothing_out():
    module = _module()
    card = build("r1-keep")
    card["description"] += "\n\nA line added after the receipt."
    assert read(card) == module.Reading(taken=True, why=module.TAKEN_WHY)


def test_a_strangers_receipt_rules_nothing_out():
    module = _module()
    for record_id in ("r1-ceo", "r1-keep"):
        card = build(record_id)
        card["comments"]["nodes"][0]["user"] = {"id": "user-person"}
        assert read(card) == module.Reading(taken=True, why=module.TAKEN_WHY), record_id


def test_rule_1_calls_keep_stands_and_never_restates_it(monkeypatch):
    module = _module()
    seen = []

    def stands(records, fingerprint):
        seen.append(fingerprint)
        return False

    monkeypatch.setattr(module.operator_decide, "keep_stands", stands)
    card = build("r1-keep")
    assert read(card).taken is True
    assert seen == [operator_decide.fingerprint(card)]


# --------------------------------------------------------------------------- #
# rule 2 to rule 7 — the edges                                                 #
# --------------------------------------------------------------------------- #


def test_a_critic_record_that_is_not_a_pass_is_taken():
    module = _module()
    card = push(decision_card("gl-stopped-run"),
                "plan-critic: stage=post round=1 result=SEND_BACK collisions=0 — a finding",
                "2026-11-02T17:00:00.000Z")
    assert read(card) == module.Reading(taken=True, why=module.TAKEN_WHY)


def test_a_strangers_pass_record_is_not_a_plan():
    module = _module()
    card = build("r2-plan")
    card["comments"]["nodes"][0]["user"] = {"id": "user-person"}
    assert read(card).why != module.PLAN_WHY


def test_an_edited_console_answer_is_not_his():
    _needs_openssl("r4-signed")
    module = _module()
    card = build("r4-signed")
    newest = card["comments"]["nodes"][0]
    newest["body"] = newest["body"].replace("Re-run the review.", "Settle it by hand.")
    assert read(card) == module.Reading(taken=True, why=module.TAKEN_WHY)


def test_a_signed_answer_older_than_the_newest_park_rules_nothing_out():
    _needs_openssl("r4-signed")
    module = _module()
    card = build("r4-signed")
    escalation = next(n for n in decision_card("gl-stopped-run")["comments"]["nodes"]
                      if n["body"].startswith("🙋"))
    card = push(card, escalation["body"], "2026-11-02T20:30:00.000Z")
    assert read(card) == module.Reading(taken=True, why=module.TAKEN_WHY)


def test_an_unscreened_planner_question_is_taken():
    """The escalation-choices block is what rule 7 reads: the same live
    escalation without it is taken."""
    module = _module()
    assert read(decision_card("gl-stopped-run")).taken is True
    card = build("r7-screened-2498")
    newest = card["comments"]["nodes"][0]
    block = newest["body"].index("```escalation-choices")
    newest["body"] = newest["body"][:block].rstrip()
    assert read(card) == module.Reading(taken=True, why=module.TAKEN_WHY)


def test_a_block_the_screen_refuses_is_taken():
    module = _module()
    card = build("r7-screened-2498")
    newest = card["comments"]["nodes"][0]
    body = newest["body"]
    start = body.index("```escalation-choices\n") + len("```escalation-choices\n")
    end = body.rindex("\n```")
    block = json.loads(body[start:end])
    block["choices"] = block["choices"][:1]
    newest["body"] = body[:start] + json.dumps(block, indent=2) + body[end:]
    assert read(card) == module.Reading(taken=True, why=module.TAKEN_WHY)


def test_a_strangers_park_comments_are_no_notice():
    """`design_answer` and `code_owner_hold` read only what the pipeline
    posted, so a copy of either park by anyone else rules nothing out."""
    module = _module()
    for record_id in ("r6-design", "r6-owner"):
        card = build(record_id)
        card["comments"]["nodes"][0]["user"] = {"id": "user-person"}
        assert read(card) == module.Reading(taken=True, why=module.TAKEN_WHY), record_id


# --------------------------------------------------------------------------- #
# rule 5 — the hygiene agent's rows                                            #
# --------------------------------------------------------------------------- #


def _hygiene_cards() -> list:
    return copy.deepcopy(_json(HYGIENE)["lanes"][LANE])


def _hygiene_ctx():
    doc = _json(HYGIENE)

    def gql(query, variables=None):
        if "viewer" in query:
            return {"viewer": {"id": PIPELINE_USER}}
        return copy.deepcopy(doc["linear"][(variables or {}).get("id")])

    def gh(argv):
        raise AssertionError(f"the Green Light lane reads no gh: {argv}")

    return hygiene.make_context("dreadnought-foundry", gh=gh,
                                linear=hygiene.read_only_linear(gql), now=HYGIENE_NOW,
                                summary_card="DRE-900")


def test_every_card_the_hygiene_lane_resends_is_not_taken():
    module = _module()
    ctx = _hygiene_ctx()
    resent = []
    for card in _hygiene_cards():
        item = hygiene_green_light.plan_card(card, ctx, PIPELINE_USER)
        reading = read(card)
        if isinstance(item, hygiene.Action):
            resent.append(card["identifier"])
            assert reading.taken is False, card["identifier"]
            assert reading.why in (module.HYGIENE_WHY, module.PLAN_WHY), card["identifier"]
    assert len(resent) == 7


def test_a_second_stall_the_hygiene_lane_leaves_is_still_not_taken():
    module = _module()
    ctx = _hygiene_ctx()
    card = next(c for c in _hygiene_cards() if c["identifier"] == "DRE-9106")
    prior = hygiene.receipt(hygiene_green_light.ACT,
                            f"{hygiene_green_light.STALL_CAUSE}2026-09-30 05:00 PT",
                            ["stall park comment 2026-09-30T12:00:00.000Z"],
                            HYGIENE_NOW - timedelta(hours=1))
    newest = card["comments"]["nodes"][0]
    card["comments"]["nodes"].insert(1, {"body": prior,
                                         "createdAt": "2026-09-30T15:30:00.000Z",
                                         "user": {"id": PIPELINE_USER}})
    assert newest["createdAt"] > "2026-09-30T15:30:00.000Z"
    assert isinstance(hygiene_green_light.plan_card(card, ctx, PIPELINE_USER), hygiene.Left)
    assert read(card) == module.Reading(taken=False, why=module.HYGIENE_WHY)


def test_a_non_plan_limit_death_is_left_by_hygiene_and_taken_here():
    ctx = _hygiene_ctx()
    card = build("taken-limit")
    item = hygiene_green_light.plan_card(card, ctx, PIPELINE_USER)
    assert isinstance(item, hygiene.Left) and "build-stage limit death" in item.why
    assert read(card) == expected("taken-limit")


# --------------------------------------------------------------------------- #
# the fixture                                                                  #
# --------------------------------------------------------------------------- #


def test_the_fixture_holds_the_seventeen_rows_with_their_keys():
    records = _records()
    assert set(records) == set(EXPECTED)
    for record_id, record in records.items():
        assert set(record) == RECORD_KEYS, record_id
        assert record["source"] in ("DRE-2498", "DRE-6589"), record_id
        assert record["copied"] == "2026-11-03", record_id
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", record["cut_after"]), record_id
        for key in ("added", "removed"):
            assert isinstance(record[key], list) and record[key], (record_id, key)
            assert all(isinstance(line, str) and line.strip() for line in record[key])
        assert isinstance(record["withheld"], list), record_id
        assert record["serves"] == ("taken" if record_id == "taken-limit"
                                    else f"rule {record_id[1]}"), record_id
        assert build(record_id)["state"] == {"name": LANE}, record_id


def test_base_b_is_never_copied_again():
    """A record built on base B carries only what the builder wrote: no
    description, no identifier — those come from the decision card's fixture."""
    bases = [card["description"] for card in
             (decision_card("gl-stopped-run"), decision_card("gl-answerable"))]
    for record_id, record in _records().items():
        assert set(record["card"]) <= {"title", "labels", "comments"}, record_id
        said = json.dumps(record["card"], ensure_ascii=False)
        assert not any(base[:200] in said for base in bases), record_id


def test_the_live_rows_name_their_cut():
    records = _records()
    assert (records["r2-plan"]["source"], records["r2-plan"]["cut_after"]) == \
        ("DRE-6589", "2026-10-11T17:51:34.146Z")
    newest = build("r2-plan")["comments"]["nodes"][0]
    assert newest["createdAt"] == "2026-10-11T17:51:34.146Z"
    assert newest["body"].startswith("plan-critic: stage=post round=2 result=PASS")
    for record_id, at in (("r7-screened-2498", "2026-10-13T15:45:46.817Z"),
                          ("r7-screened-6589", "2026-10-10T09:58:16.475Z")):
        assert records[record_id]["cut_after"] == at
        newest = build(record_id)["comments"]["nodes"][0]
        assert newest["createdAt"] == at
        assert "```escalation-choices" in newest["body"], record_id


def _rules_test():
    spec = importlib.util.spec_from_file_location("_operator_rules_tests", RULES_TEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_fixture_is_held_to_part_ones_limits(monkeypatch):
    rules_test = _rules_test()
    monkeypatch.setattr(rules_test, "FIXTURE", FIXTURE)
    rules_test.test_the_fixture_names_no_address_link_or_account_number()
    rules_test.test_the_fixture_names_the_ceo_and_no_one_else()


def test_part_ones_limits_do_catch_a_leak(tmp_path, monkeypatch):
    rules_test = _rules_test()
    leaked = tmp_path / "leaked.json"
    leaked.write_text(json.dumps({"body": "write to someone@corp.example.org"}),
                      encoding="utf-8")
    monkeypatch.setattr(rules_test, "FIXTURE", leaked)
    with pytest.raises(AssertionError):
        rules_test.test_the_fixture_names_no_address_link_or_account_number()


# --------------------------------------------------------------------------- #
# purity                                                                       #
# --------------------------------------------------------------------------- #


def test_reading_makes_no_network_call(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a network call")

    def no_key():
        raise AssertionError("the console's key was fetched")

    module = _module()
    monkeypatch.setattr(linear_ops, "gql", refuse)
    monkeypatch.setattr(linear_ops, "_thread_and_viewer", refuse)
    monkeypatch.setattr(linear_ops, "viewer_id", refuse)
    monkeypatch.setattr(linear_ops, "comment_records", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(console_receipt, "fetch_key", no_key)
    monkeypatch.setattr(spoken_thread, "_VERIFIER", console_receipt.Verifier(
        key_loader=lambda: console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)))
    cards = [build(r) for r in EXPECTED] + [decision_card("gl-stopped-run"),
                                            decision_card("gl-answerable"),
                                            _kept_act_card()] + _hygiene_cards()
    for card in cards:
        assert isinstance(read(card), module.Reading)


def test_the_module_names_no_person():
    text = MODULE.read_text(encoding="utf-8") + Path(__file__).read_text(encoding="utf-8")
    pattern = re.search(r'not re\.search\(r"([^"]+)", text\)',
                        RULES_TEST.read_text(encoding="utf-8")).group(1)
    assert not re.search(pattern, text)
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                         MODULE.read_text(encoding="utf-8"))
