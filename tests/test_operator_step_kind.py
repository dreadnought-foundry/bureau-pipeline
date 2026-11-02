"""RED-first: what kind of operator step a Hand-work card is (DRE-8650).

`scripts/operator_step_kind.py` reads a card's title and description and says
whether a machine could do its step or a person must: `classify(title,
description) -> (class, kind, evidence)`. The rule is data,
`config/operator-step-kinds.json` — the phrases that name a person acting, and
an ordered list of machine kinds, each with the phrases that identify it.

Precedence: a person phrase anywhere wins; otherwise the first machine kind
matched wins; with no match the class is `person` and the evidence says no
machine kind matched.

The fixtures are copied from the live cards the CEO named on 2026-11-01: each
card's title, and the line of its description that names its step, as Linear
answered them on 2026-11-02.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_step_kind.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

MODULE_PATH = ROOT / "scripts" / "operator_step_kind.py"
CONFIG_PATH = ROOT / "config" / "operator-step-kinds.json"

MACHINE_KINDS = {"deploy", "script", "variable", "linear-admin", "bot-pr"}

#: The person phrases, exactly as the card writes them.
PERSON_PHRASES = [
    "signed-in console session", "own signed-in session", "signs in",
    "the operator's Mac", "on the operator's machine", "with the mode pill",
    "from their own session", "from his own session", "the CEO decides",
]


def load():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("operator_step_kind", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kind_module = load()


def classify(title, description):
    assert kind_module is not None, f"{MODULE_PATH.name} does not exist yet"
    return kind_module.classify(title, description)


# --------------------------------------------------------------------------- #
# the live cards                                                               #
# --------------------------------------------------------------------------- #

#: (card, title, the description line that names its step, class, kind)
LIVE = [
    ("DRE-7328",
     "OPERATOR: set NO_RECORD_RETURN_MODE=enforce on the live relay, then close this "
     "card through the console's Record the outcome",
     "Turn the relay's no-record return on, live. The code ships behind "
     "`NO_RECORD_RETURN_MODE`, which defaults to `off`, and only the bootstrap deploy "
     "sets a relay environment variable — it needs the operator's AWS profile, which "
     "no agent holds. This is an operator step, not code.",
     "machine", "variable"),
    ("DRE-7579",
     "agent-bureau: archive the Linear project and Initiative of retired onboard-sandbox",
     "Archiving a Linear project or Initiative is operator-only, so the process recorded "
     "these under `board` in `config/pipeline-channel.json` and leaves them here.",
     "machine", "linear-admin"),
    ("DRE-7942",
     "agent-bureau: archive the Linear project and Initiative of retired onboard-sandbox-4",
     "* The Linear project `onboard-sandbox-4: Onboard Sandbox 4` — put it in a terminal "
     "state and archive it.",
     "machine", "linear-admin"),
    ("DRE-7301",
     "portico: the content buckets' CORS rule has never been applied — run "
     "60-content-bucket-cors.sh in both accounts, late at night",
     "1. `bash infra/cdn/60-content-bucket-cors.sh --account dreadnought --profile "
     "dreadnought --dry-run` — read the diff; confirm it matches the list above.",
     "machine", "script"),
    ("DRE-7978",
     "bureau-pipeline: the operator opens one throwaway pull request that breaks the "
     "verify step, sees the replay gate hold it, and closes it unmerged",
     "1. From `main`, open one draft pull request on a branch named "
     "`operator/replay-gate-negative-control`. It changes only `briefs/groom-verify.md`, "
     "adding one line telling the verify agent to answer `still-needed` for every card. "
     "The title says it is a negative control and must not be merged.",
     "machine", "bot-pr"),
    ("DRE-8189",
     "agent-bureau: operator files the card that onboards the throwaway repo "
     "onboard-sandbox-5, declared to release an app surface",
     "1. Once DRE-8187 has merged and DRE-8188 is Done, file one card in `Intake`, "
     "labelled `repo:agent-bureau`, asking to onboard a new throwaway repo "
     "`onboard-sandbox-5` (display name \"Onboard Sandbox 5\") in `dreadnought-foundry`.",
     "machine", "linear-admin"),
    ("DRE-8341",
     "portico: after merge, restamp the sign-in pages on all nine portals, create the "
     "mixed-case sandbox proof identity, and read how the pool's email filter treats "
     "letter case",
     "1. **Restamp the sign-in pages on all nine portals**, so the lowercasing "
     "`comments.js` from DRE-8339 reaches them. `comments.js` is one of the five "
     "`AUTH_FILES`, and neither the train nor `cdk deploy` ships them.",
     "machine", "deploy"),
    ("DRE-7874",
     "OPERATOR: install the launcher that reads the model home, and the five inherit "
     "charters, on the operator's Mac",
     "This is that refresh, done by the operator session on the Mac once both cards "
     "have merged.",
     "person", "person"),
    ("DRE-8495",
     "agent-bureau: on the live sandbox project, the operator sets the Full-Stack "
     "Engineer Desk pair to Auto, once",
     "The operator does it from their own signed-in console session, under the "
     "operator's standing permission of 2026-10-07.",
     "person", "person"),
]


class TestTheLiveCards:
    @pytest.mark.parametrize("card,title,line,cls,kind", LIVE, ids=[row[0] for row in LIVE])
    def test_each_live_card_classes_as_the_ceo_read_it(self, card, title, line, cls, kind):
        got_cls, got_kind, evidence = classify(title, line)
        assert (got_cls, got_kind) == (cls, kind), (card, evidence)
        assert evidence and evidence.strip()

    @pytest.mark.parametrize("card,title,line,cls,kind", LIVE, ids=[row[0] for row in LIVE])
    def test_the_evidence_is_text_the_card_carries(self, card, title, line, cls, kind):
        _cls, _kind, evidence = classify(title, line)
        assert evidence.lower() in f"{title}\n{line}".lower(), (card, evidence)

    def test_dre_8341_sign_in_pages_is_a_machine_deploy(self):
        title, line = LIVE[6][1], LIVE[6][2]
        assert "sign-in pages" in title
        cls, kind, evidence = classify(title, line)
        assert (cls, kind) == ("machine", "deploy")
        assert "sign" not in evidence.lower()

    def test_dre_8495_own_signed_in_console_session_is_a_person(self):
        title, line = LIVE[8][1], LIVE[8][2]
        assert "own signed-in console session" in line
        cls, kind, evidence = classify(title, line)
        assert (cls, kind) == ("person", "person")
        assert evidence == "signed-in console session"

    def test_dre_7874_the_operators_mac_is_a_person(self):
        cls, kind, evidence = classify(LIVE[7][1], LIVE[7][2])
        assert (cls, kind, evidence) == ("person", "person", "the operator's Mac")

    def test_dre_7328_a_variable_set_by_a_deploy_is_a_variable(self):
        # Its line names a deploy too; the variable is the step, so the
        # ordered list puts the narrower kind first.
        cls, kind, evidence = classify(LIVE[0][1], LIVE[0][2])
        assert "deploy" in LIVE[0][2]
        assert (cls, kind, evidence) == ("machine", "variable", "environment variable")


# --------------------------------------------------------------------------- #
# precedence                                                                   #
# --------------------------------------------------------------------------- #


class TestPrecedence:
    def test_a_person_phrase_beats_a_machine_phrase_and_is_quoted(self):
        cls, kind, evidence = classify(
            "portico: deploy the console",
            "Run `cdk deploy` from the operator's Mac, then read the stack back.")
        assert (cls, kind) == ("person", "person")
        assert evidence == "the operator's Mac"

    def test_a_person_phrase_in_the_title_beats_a_machine_phrase_in_the_body(self):
        cls, kind, evidence = classify(
            "the operator signs in and checks the portal",
            "Run `bash infra/cdn/60-content-bucket-cors.sh` first.")
        assert (cls, kind, evidence) == ("person", "person", "signs in")

    def test_no_phrase_at_all_is_a_person_with_no_machine_kind_matched(self):
        cls, kind, evidence = classify(
            "agent-bureau: write down what the onboarding call agreed",
            "Read the notes and say what was decided.")
        assert (cls, kind, evidence) == ("person", "person", "no machine kind matched")

    def test_empty_text_is_a_person_with_no_machine_kind_matched(self):
        assert classify("", None) == ("person", "person", "no machine kind matched")

    def test_the_first_machine_kind_in_the_list_wins(self):
        doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        order = [row["kind"] for row in doc["kinds"]]
        # One phrase of every kind, written in REVERSE list order, so a rule
        # that took the first phrase in the text would pick the last kind.
        text = " and ".join(row["phrases"][0] for row in reversed(doc["kinds"]))
        cls, kind, _evidence = classify("synthetic", text)
        assert (cls, kind) == ("machine", order[0])

    @pytest.mark.parametrize("phrase", PERSON_PHRASES)
    def test_every_person_phrase_names_a_person(self, phrase):
        cls, kind, evidence = classify("synthetic", f"Then {phrase} and it is done.")
        assert (cls, kind) == ("person", "person")
        assert evidence.lower() == phrase.lower()


# --------------------------------------------------------------------------- #
# how a phrase matches                                                         #
# --------------------------------------------------------------------------- #


class TestWholePhrases:
    @pytest.mark.parametrize("text", [
        "Restamp the sign-in pages",
        "the sign-in page is broken",
        "after sign in, nothing",
        "a signed-in session",
    ])
    def test_a_mention_of_signing_in_is_not_a_person(self, text):
        cls, _kind, evidence = classify("synthetic", text)
        if cls == "person":
            assert evidence == "no machine kind matched"

    def test_case_does_not_matter(self):
        assert classify("synthetic", "done on THE OPERATOR'S MAC")[0] == "person"
        assert classify("synthetic", "done on The Operator's Mac")[2] == "The Operator's Mac"

    def test_a_curly_apostrophe_matches(self):
        cls, _kind, evidence = classify("synthetic", "installed on the operator’s Mac")
        assert cls == "person"
        assert evidence == "the operator's Mac"

    def test_a_phrase_inside_a_longer_word_does_not_match(self):
        # `signs in` must not fire on `designs into`.
        cls, _kind, evidence = classify("synthetic", "the team designs interfaces")
        assert (cls, evidence) == ("person", "no machine kind matched")

    def test_whitespace_inside_a_phrase_is_flexible(self):
        cls, _kind, evidence = classify("synthetic", "from their own\nsession")
        assert cls == "person"
        assert evidence == "from their own\nsession"


# --------------------------------------------------------------------------- #
# the data                                                                     #
# --------------------------------------------------------------------------- #


class TestTheData:
    def test_the_config_names_the_five_machine_kinds_once_each(self):
        doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        kinds = [row["kind"] for row in doc["kinds"]]
        assert sorted(kinds) == sorted(MACHINE_KINDS)
        assert all(row["phrases"] and all(p.strip() for p in row["phrases"])
                   for row in doc["kinds"])

    def test_the_person_phrases_are_exactly_the_cards(self):
        doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        assert doc["person"]["phrases"] == PERSON_PHRASES

    def test_a_bare_sign_in_is_not_a_person_phrase(self):
        doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        assert not {"sign-in", "sign in"} & {p.lower() for p in doc["person"]["phrases"]}

    def test_the_rule_is_read_from_the_data(self, tmp_path):
        doc = {"person": {"phrases": ["by hand"]},
               "kinds": [{"kind": "script", "phrases": ["frobnicate"]}]}
        path = tmp_path / "kinds.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        rules = kind_module.load(path)
        assert kind_module.classify("synthetic", "frobnicate it", rules) == (
            "machine", "script", "frobnicate")
        assert kind_module.classify("synthetic", "frobnicate it by hand", rules) == (
            "person", "person", "by hand")

    def test_a_kind_outside_the_five_is_refused(self, tmp_path):
        doc = {"person": {"phrases": ["by hand"]},
               "kinds": [{"kind": "magic", "phrases": ["abracadabra"]}]}
        path = tmp_path / "kinds.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(ValueError, match="magic"):
            kind_module.load(path)
