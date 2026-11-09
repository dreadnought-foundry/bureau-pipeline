"""RED-first tests: the operator agent's fixed routes (DRE-8743).

Some cards waiting on a person must reach the CEO, or the studio host, whatever
a model reads into them. `config/operator-agent.json` declares those routes
once, and `scripts/operator_rules.py` is the pure module that reads them. These
tests hold the table's shape, the module's purity, the replay fixture's
excerpt-only form (DRE-3918), and the replay over the board of 2026-11-01.

What the replay proves is the rules' PRECISION: the full set of fixture cards
the rules send to the CEO, and to the studio host, is pinned below by id, act
and the phrase quoted from the card. It does not prove recall — the live pass
in the epic's PROOF, DRE-8837, is the test of that.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_rules.py -v
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import operator_rules  # noqa: E402

CONFIG = ROOT / "config" / "operator-agent.json"
FIXTURE = ROOT / "tests" / "fixtures" / "operator-agent-board-2026-11-01.json"
MODULE = ROOT / "scripts" / "operator_rules.py"

KEPT_ACTS = {
    "aws-access", "billing-and-account-settings", "github-org-membership",
    "github-secrets", "github-branch-protection", "github-org-creation",
}

HOST_LIMITS = [
    "In this child the studio user can do everything but deploy.",
    "Its boundary denies every IAM write.",
    "Its boundary denies billing and account settings.",
    "Its boundary denies secret writes and secret values.",
    "Its boundary denies every CloudFormation write.",
]

HAND_WORK = ("DRE-7290 DRE-7298 DRE-7301 DRE-7328 DRE-7620 DRE-7886 DRE-7909 "
             "DRE-7933 DRE-8173 DRE-8227 DRE-8331 DRE-8341 DRE-8414 DRE-8431 "
             "DRE-8537 DRE-8643 DRE-8656 DRE-8664 DRE-8671").split()
INTAKE = ("DRE-4434 DRE-8090 DRE-8091 DRE-8092 DRE-8093 DRE-8094 DRE-8095 "
          "DRE-8096 DRE-8097 DRE-8098 DRE-8099 DRE-8100 DRE-8307 DRE-8308 "
          "DRE-8309 DRE-8314").split()
QUEUED = ("DRE-7078 DRE-7083 DRE-7223 DRE-7224 DRE-7307 DRE-7621 DRE-8190 "
          "DRE-8335 DRE-8343 DRE-8528 DRE-8582 DRE-8623 DRE-8637").split()
CLOSED_ON_THE_DAY = ["DRE-7579", "DRE-7942", "DRE-7978"]

#: Every fixture card the rules send to the CEO: card -> (act, phrase quoted
#: from the card). Each phrase was read on the card before it went in the
#: table; a card added here is a kept act the CEO has to see.
CEO_SET = {
    "DRE-7078": ("destructive-go", "force-push … go again right before"),
    "DRE-7224": ("aws-access", "AWS role"),
    "DRE-8094": ("billing-and-account-settings", "own AWS account"),
    "DRE-8173": ("github-org-membership", "GitHub App permission"),
    "DRE-8227": ("github-org-creation", "throwaway org"),
    "DRE-8582": ("aws-access", "AWS identity"),
    "DRE-8643": ("aws-access", "IAM change"),
}

#: Every fixture card the rules send to the studio host: card -> (phrase,
#: deploy).
STUDIO_SET = {
    "DRE-7223": ("production deploy", True),
    "DRE-7328": ("the operator's AWS profile", True),
}


def _table() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _write(tmp_path: Path, table: dict) -> Path:
    path = tmp_path / "operator-agent.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    return path


def _refused(tmp_path: Path, table: dict) -> str:
    with pytest.raises(ValueError) as caught:
        operator_rules.load_rules(_write(tmp_path, table))
    return str(caught.value)


def _fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _by_card() -> dict:
    return {record["card"]: operator_rules.fixture_card(record) for record in _fixture()}


def _outcome(card: dict):
    return operator_rules.rule_outcome(card["title"], card["description"])


# --- the table ------------------------------------------------------------


def test_the_table_holds_the_six_keys_and_loads():
    assert set(_table()) == {"ceo_acts", "destructive_go", "studio_access",
                             "deploy", "studio", "leave_alone"}
    rules = operator_rules.load_rules()
    assert {act["id"] for act in rules["ceo_acts"]} == KEPT_ACTS


def test_the_default_path_is_the_config_file():
    assert operator_rules.load_rules() == operator_rules.load_rules(CONFIG)


def test_load_rules_refuses_an_unknown_key(tmp_path):
    table = _table()
    table["extra"] = []
    assert "extra" in _refused(tmp_path, table)


def test_load_rules_refuses_a_missing_key(tmp_path):
    table = _table()
    del table["deploy"]
    assert "deploy" in _refused(tmp_path, table)


@pytest.mark.parametrize("where", [
    ("ceo_acts", 0), ("destructive_go", "destructive"), ("destructive_go", "go"),
    ("studio_access",), ("deploy",),
])
def test_load_rules_refuses_an_empty_phrase_list(tmp_path, where):
    table = _table()
    if where[0] == "ceo_acts":
        table["ceo_acts"][where[1]]["phrases"] = []
    elif len(where) == 2:
        table[where[0]][where[1]] = []
    else:
        table[where[0]] = []
    assert "phrase" in _refused(tmp_path, table)


def test_load_rules_refuses_an_act_id_outside_the_six(tmp_path):
    table = _table()
    table["ceo_acts"][0]["id"] = "business-choice"
    assert "business-choice" in _refused(tmp_path, table)


def test_load_rules_refuses_an_act_named_twice(tmp_path):
    table = _table()
    table["ceo_acts"][1]["id"] = table["ceo_acts"][0]["id"]
    assert f"the act {table['ceo_acts'][0]['id']} is named twice" in _refused(tmp_path, table)


@pytest.mark.parametrize("field", ["card", "why"])
def test_load_rules_refuses_a_leave_alone_record_without_both_fields(tmp_path, field):
    table = _table()
    del table["leave_alone"][0][field]
    assert field in _refused(tmp_path, table)


def test_load_rules_refuses_host_limits_without_a_source(tmp_path):
    table = _table()
    del table["studio"]["host_limits"]["source"]
    assert "source" in _refused(tmp_path, table)


def test_load_rules_refuses_host_limits_with_an_empty_cannot(tmp_path):
    table = _table()
    table["studio"]["host_limits"]["cannot"] = []
    assert "cannot" in _refused(tmp_path, table)


def test_the_kept_acts_are_exactly_the_six_ids_with_a_label_each():
    acts = _table()["ceo_acts"]
    assert sorted(act["id"] for act in acts) == sorted(KEPT_ACTS)
    for act in acts:
        assert set(act) == {"id", "label", "phrases"}
        assert act["label"].strip()


def test_leave_alone_holds_exactly_the_three_switch_steps_and_the_proof_fixture():
    rows = _table()["leave_alone"]
    assert [row["card"] for row in rows] == ["DRE-8748", "DRE-8830", "DRE-8927",
                                             "DRE-9053"]
    assert rows[0]["why"] == ("the switch that turns this agent on — a person "
                              "closes it once the first live run's URL is "
                              "recorded on it")
    assert rows[1]["why"] == ("the switch's off-reason — a person sets it before "
                              "the workflow merges, and DRE-8748 deletes the "
                              "variable it sets when it turns the agent on")
    assert rows[2]["why"] == ("the switch that turns the agent's `do` step on "
                              "(OPERATOR_AGENT_DO_LIVE, DRE-8928) — the agent "
                              "never decides it")
    assert rows[3]["why"] == ("the proof's fixture step — a person files one "
                              "sandbox question in Green Light and cancels it "
                              "once its planning run has finished; the agent "
                              "never decides the card that sets up its own "
                              "proof")


def test_the_studio_ids_are_read_from_the_table():
    studio = _table()["studio"]
    assert studio["blocker"] == "DRE-8582"
    assert studio["deploy_epic"] == "DRE-8487"
    assert studio["never_blocker"] == "DRE-8486"


def test_host_limits_carry_the_source_the_day_and_the_five_lines():
    limits = _table()["studio"]["host_limits"]
    assert set(limits) == {"source", "read_on", "cannot"}
    assert limits["source"] == "agent-bureau:infra/deploy/stacks/StudioDeskStack.md"
    assert limits["read_on"] == "2026-11-01 PT"
    assert limits["cannot"] == HOST_LIMITS


def test_host_limits_text_joins_the_cannot_lines_by_newlines():
    assert operator_rules.host_limits_text() == "\n".join(HOST_LIMITS)


def test_host_limits_text_reads_the_rules_it_is_given():
    rules = copy.deepcopy(operator_rules.load_rules())
    rules["studio"]["host_limits"]["cannot"] = ["one", "two"]
    assert operator_rules.host_limits_text(rules) == "one\ntwo"


def test_every_phrase_is_quoted_from_a_fixture_excerpt():
    excerpts = " \n ".join(excerpt["text"] for record in _fixture()
                           for excerpt in record["excerpts"]).lower()
    table = _table()
    phrases = [p for act in table["ceo_acts"] for p in act["phrases"]]
    phrases += table["destructive_go"]["destructive"] + table["destructive_go"]["go"]
    phrases += table["studio_access"] + table["deploy"]
    missing = [p for p in phrases if p.lower() not in excerpts]
    assert not missing, f"phrases quoted from no fixture excerpt: {missing}"


# --- purity ---------------------------------------------------------------


def test_the_module_makes_no_network_call_and_runs_nothing(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("operator_rules reached outside the repository")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    rules = operator_rules.load_rules()
    assert operator_rules.host_limits_text(rules)
    assert operator_rules.host_limits_text()
    operator_rules.rule_outcome("a title", "a description", rules)


def test_the_module_names_no_other_repositorys_file_to_read():
    source = MODULE.read_text(encoding="utf-8")
    assert "StudioDeskStack" not in source
    assert "GH_READ_TOKEN" not in source
    assert "urllib" not in source and "subprocess" not in source


# --- the fixture is excerpts, never copies (DRE-3918) ---------------------


def test_the_fixture_holds_exactly_the_51_cards():
    cards = [record["card"] for record in _fixture()]
    assert len(cards) == len(set(cards)) == 51
    assert set(cards) == set(HAND_WORK + INTAKE + QUEUED + CLOSED_ON_THE_DAY)


def test_each_card_carries_the_lane_it_was_in_on_the_day():
    lanes = {record["card"]: record["lane"] for record in _fixture()}
    for card in HAND_WORK + CLOSED_ON_THE_DAY:
        assert lanes[card] == "Hand-work", card
    for card in INTAKE:
        assert lanes[card] == "Intake", card
    for card in QUEUED:
        assert lanes[card] == "Queued", card


def test_the_fixture_holds_excerpts_and_nothing_more():
    for record in _fixture():
        assert set(record) == {"card", "lane", "title", "labels", "excerpts"}, record["card"]
        assert isinstance(record["labels"], list)
        assert 1 <= len(record["excerpts"]) <= 3, record["card"]
        for excerpt in record["excerpts"]:
            assert set(excerpt) == {"from", "created_at", "text"}, record["card"]
            assert excerpt["from"] in ("description", "comment"), record["card"]
            if excerpt["from"] == "description":
                assert excerpt["created_at"] is None, record["card"]
            else:
                assert re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", excerpt["created_at"])
            text = excerpt["text"]
            assert text.strip() and len(text) <= 300, record["card"]
            assert "\n" not in text and "\r" not in text, record["card"]


def test_the_fixture_names_no_address_link_or_account_number():
    text = FIXTURE.read_text(encoding="utf-8")
    emails = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))
    assert emails <= {"person@example.com"}
    for url in re.findall(r"https?://[^\s)\"\]>]+", text):
        assert url.startswith(("https://github.com/dreadnought-foundry/",
                               "https://linear.app/dreadnoughtfoundry/")), url
    assert not re.search(r"\d{12}", text)


def test_the_fixture_names_the_ceo_and_no_one_else():
    text = FIXTURE.read_text(encoding="utf-8")
    assert not re.search(r"\bSid\b|Conklin", text)


# --- fixture_card ---------------------------------------------------------


def test_fixture_card_returns_the_card_shape_the_siblings_read():
    record = next(r for r in _fixture() if r["card"] == "DRE-7328")
    card = operator_rules.fixture_card(record)
    assert set(card) == {"card", "lane", "title", "labels", "description", "comments"}
    assert card["card"] == "DRE-7328" and card["lane"] == "Hand-work"
    assert card["title"] == record["title"] and card["labels"] == record["labels"]
    described = [e["text"] for e in record["excerpts"] if e["from"] == "description"]
    assert len(described) == 2
    assert card["description"] == "\n\n".join(described)
    assert [c["body"] for c in card["comments"]] == [
        e["text"] for e in record["excerpts"] if e["from"] == "comment"]
    for comment in card["comments"]:
        assert set(comment) == {"author_kind", "created_at", "body"}


def test_dre_4434_carries_the_atlas_is_live_comment():
    record = next(r for r in _fixture() if r["card"] == "DRE-4434")
    comments = [e for e in record["excerpts"] if e["from"] == "comment"]
    atlas = [e for e in comments if e["text"].startswith("Atlas is live, 2026-09-21 11:07 PT.")]
    assert len(atlas) == 1 and atlas[0]["created_at"].startswith("2026-09-21")
    card = operator_rules.fixture_card(record)
    assert {"author_kind": operator_rules.FIXTURE_AUTHOR_KIND,
            "created_at": atlas[0]["created_at"],
            "body": atlas[0]["text"]} in card["comments"]
    assert "Atlas is live" not in card["description"]


# --- the replay over the board of 2026-11-01 ------------------------------


def test_the_named_cards_route_as_the_epic_names_them():
    cards = _by_card()
    match = _outcome(cards["DRE-8173"])
    assert (match.outcome, match.act) == ("ceo", "github-org-membership")
    match = _outcome(cards["DRE-8227"])
    assert (match.outcome, match.act) == ("ceo", "github-org-creation")
    match = _outcome(cards["DRE-7078"])
    assert (match.outcome, match.act) == ("ceo", "destructive-go")
    match = _outcome(cards["DRE-7328"])
    assert (match.outcome, match.act) == ("studio", "")


def test_the_three_cards_closed_on_the_day_match_no_rule():
    cards = _by_card()
    for card in CLOSED_ON_THE_DAY:
        assert _outcome(cards[card]) is None, card


def test_the_full_ceo_set_is_pinned_with_act_and_phrase():
    routed = {}
    for card_id, card in _by_card().items():
        match = _outcome(card)
        if match is not None and match.outcome == "ceo":
            routed[card_id] = (match.act, match.phrase)
    assert routed == CEO_SET
    for act, _phrase in routed.values():
        assert act in KEPT_ACTS | {"destructive-go"}


def test_the_full_studio_set_is_pinned_with_phrase_and_deploy():
    routed = {}
    for card_id, card in _by_card().items():
        match = _outcome(card)
        if match is not None and match.outcome == "studio":
            routed[card_id] = (match.phrase, match.deploy)
    assert routed == STUDIO_SET


def test_each_ceo_phrase_is_quoted_from_its_own_card():
    cards = _by_card()
    for card_id, (_act, phrase) in CEO_SET.items():
        text = f"{cards[card_id]['title']}\n{cards[card_id]['description']}"
        for piece in phrase.split(" … "):
            assert piece in text, (card_id, piece)


def test_a_studio_match_that_names_a_deploy_step_says_so():
    match = _outcome(_by_card()["DRE-7328"])
    assert match.outcome == "studio"
    assert match.deploy is True


# --- precedence and matching ----------------------------------------------


def test_a_destructive_phrase_without_a_go_is_not_destructive_go():
    match = operator_rules.rule_outcome(
        "OPERATOR: purge the old files",
        "Run the purge, then force-push all branches, because it rewrites every commit.")
    assert match is None or match.act != "destructive-go"


def test_a_go_phrase_without_a_destructive_step_is_not_destructive_go():
    match = operator_rules.rule_outcome(
        "OPERATOR: rename the repo",
        "It needs his go again right before the rename.")
    assert match is None or match.act != "destructive-go"


def test_both_destructive_and_go_route_to_the_ceo():
    match = operator_rules.rule_outcome(
        "OPERATOR: rewrite history",
        "It needs his go again right before the force-push.")
    assert (match.outcome, match.act) == ("ceo", "destructive-go")


def test_a_kept_act_and_a_studio_phrase_route_to_the_ceo():
    match = operator_rules.rule_outcome(
        "OPERATOR: one sitting",
        "Deploy it from the operator's AWS profile; the build applies no IAM change.")
    assert (match.outcome, match.act, match.phrase) == ("ceo", "aws-access", "IAM change")


def test_a_fixture_card_with_a_kept_act_and_a_studio_phrase_is_ceo():
    card = _by_card()["DRE-8094"]
    assert re.search(r"production deploy", card["description"])
    assert _outcome(card).outcome == "ceo"


def test_a_kept_act_comes_before_destructive_go():
    match = operator_rules.rule_outcome(
        "OPERATOR: purge",
        "If branch protection blocks it, lift it, and it needs his go again right "
        "before the force-push.")
    assert (match.outcome, match.act) == ("ceo", "github-branch-protection")


def test_a_phrase_inside_fenced_code_matches_nothing():
    description = ("Run the command below.\n\n```\n"
                   "AWS_PROFILE=x # the operator's AWS profile, the IAM change, a force-push "
                   "with his go again right before\n```\n")
    assert operator_rules.rule_outcome("OPERATOR: a step", description) is None


def test_a_phrase_outside_the_fence_still_matches():
    description = ("```\nnothing here\n```\n"
                   "It needs the operator's AWS profile.")
    match = operator_rules.rule_outcome("OPERATOR: a step", description)
    assert (match.outcome, match.phrase) == ("studio", "the operator's AWS profile")


def test_phrases_match_case_insensitively_and_whole():
    match = operator_rules.rule_outcome("OPERATOR", "The build applies no iam CHANGE here.")
    assert (match.act, match.phrase) == ("aws-access", "iam CHANGE")
    assert operator_rules.rule_outcome("OPERATOR", "a non-AWS roles list") is None


#: A plural on a phrase's last word routes as the singular does: one card per
#: kept act, plus the studio phrases — `(text, outcome, act, phrase quoted)`.
PLURALS = [
    ("Review the IAM changes first.", "ceo", "aws-access", "IAM changes"),
    ("Create two AWS roles for the host.", "ceo", "aws-access", "AWS roles"),
    ("Make AWS identities for the user.", "ceo", "aws-access", "AWS identities"),
    ("Each team runs its own AWS accounts.", "ceo", "billing-and-account-settings",
     "own AWS accounts"),
    ("Buy two paid seats.", "ceo", "billing-and-account-settings", "paid seats"),
    ("Update the GitHub App permissions.", "ceo", "github-org-membership",
     "GitHub App permissions"),
    ("Set up two throwaway orgs.", "ceo", "github-org-creation", "throwaway orgs"),
    ("Lift the branch protections.", "ceo", "github-branch-protection",
     "branch protections"),
    ("Run it from the operator's AWS profiles.", "studio", "",
     "the operator's AWS profiles"),
    ("It waits on two production deploys.", "studio", "", "production deploys"),
]


@pytest.mark.parametrize("text, outcome, act, phrase", PLURALS)
def test_a_plural_routes_as_the_singular_does(text, outcome, act, phrase):
    match = operator_rules.rule_outcome("OPERATOR: a step", text)
    assert (match.outcome, match.act, match.phrase) == (outcome, act, phrase)


def test_a_plural_on_the_destructive_step_still_meets_the_go():
    match = operator_rules.rule_outcome(
        "OPERATOR: rewrite history",
        "It needs his go again right before the force-pushes.")
    assert (match.outcome, match.act, match.phrase) == (
        "ceo", "destructive-go", "force-pushes … go again right before")


def test_a_plural_phrase_matches_its_singular():
    match = operator_rules.rule_outcome("OPERATOR", "Set the org-level secret first.")
    assert (match.act, match.phrase) == ("github-secrets", "org-level secret")
    match = operator_rules.rule_outcome("OPERATOR", "Grant the Organization permission.")
    assert (match.act, match.phrase) == ("github-org-membership", "Organization permission")


def test_every_kept_act_has_a_plural_case():
    # `org-level secrets` is already plural: its singular case is above.
    assert {act for _text, outcome, act, _phrase in PLURALS if outcome == "ceo"} \
        == KEPT_ACTS - {"github-secrets"}


@pytest.mark.parametrize("text", [
    "Write the AWS roleplay guide.",
    "Count the paid seating plan.",
    "A throwaway organ donor.",
    "The IAM changesets are listed.",
])
def test_a_longer_word_still_matches_nothing(text):
    assert operator_rules.rule_outcome("OPERATOR: a step", text) is None


def test_an_unclosed_fence_runs_to_the_end():
    assert operator_rules.rule_outcome("OPERATOR", "```\nunclosed IAM change") is None
    assert operator_rules.rule_outcome("OPERATOR", "~~~\nAWS role\n```\nIAM change") is None


def test_the_cli_prints_the_route(capsys):
    assert operator_rules.main(["OPERATOR: step", "Review the IAM change first."]) == 0
    assert capsys.readouterr().out == "ceo aws-access — IAM change\n"
    assert operator_rules.main(["OPERATOR: step", "It waits on a production deploy."]) == 0
    assert capsys.readouterr().out == "studio - — production deploy (deploy)\n"


def test_the_cli_says_when_no_rule_matches(capsys):
    assert operator_rules.main(["OPERATOR: archive a sandbox"]) == 0
    assert capsys.readouterr().out == "no rule — the model's to read\n"


@pytest.mark.parametrize("argv", [[], ["a", "b", "c"]])
def test_the_cli_prints_its_usage_and_exits_2(capsys, argv):
    assert operator_rules.main(argv) == 2
    assert capsys.readouterr().err.startswith("python3 scripts/operator_rules.py")


def test_no_match_is_none_the_card_is_the_models_to_read():
    assert operator_rules.rule_outcome("OPERATOR: archive a sandbox",
                                       "Press Archive on its Settings page.") is None
    assert operator_rules.rule_outcome(None, None) is None


def test_rule_outcome_reads_the_rules_it_is_given():
    rules = copy.deepcopy(operator_rules.load_rules())
    rules["studio_access"] = ["a live production system"]
    match = operator_rules.rule_outcome("x", "It writes to a live production system.", rules)
    assert (match.outcome, match.act, match.deploy) == ("studio", "", False)


def test_the_new_files_name_no_person_email_or_sign_in():
    for path in (CONFIG, MODULE):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bSid\b|Conklin|Frederick", text), path
        assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text), path
        assert not re.search(r"sign-?in", text, re.I), path
