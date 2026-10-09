"""TDD for the send-back finding classifier (DRE-6356).

The one-off critic writes `SEND_BACK` or `QUESTION` and nothing reads the
finding behind the word. On DRE-3879 two of the five rounds that reached the
CEO named something the card had to SAY — an unverifiable claim, two branch
names he had already given — and were sent to him as decisions.
`scripts/send_back_class.py` is the one reader that tells a `revision` (a
rewrite fixes it) from a `decision` (only the CEO can make it), and these tests
drive it with the findings the critic really wrote on DRE-3879 and DRE-3880,
verbatim, from `tests/fixtures/send-back-findings.json`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_send_back_class.py -v
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
MODULE = SCRIPTS / "send_back_class.py"
FIXTURE = REPO / "tests" / "fixtures" / "send-back-findings.json"
sys.path.insert(0, str(SCRIPTS))

import send_back_class as sbc  # noqa: E402


def _records() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _finding(card: str | None, round_: int | None) -> str:
    for r in _records():
        if r["card"] == card and r["round"] == round_:
            return r["finding"]
    raise AssertionError(f"no fixture record for {card} round {round_}")


# A finding about the weather names nothing the card must say and no call
# anybody owes — it matches neither list.
NEITHER = "The weather in the build region was pleasant all week."

# Names a decision nobody has made AND complains that the decision the card
# claims is unverified. The provenance complaint wins: the remedy is re-stating
# the answer through the signed channel, never a judgement.
BOTH_WITH_PROVENANCE = (
    "The card says the CEO picked the narrower fix, but that answer is not "
    "confirmed to be from the CEO, and until it is, nobody has made that call."
)


# --- The contract the readers import (DRE-6359, DRE-6380, DRE-6452) --------

class TestFrozenContractForDre6359Dre6380Dre6452:
    """DRE-6359 and DRE-6452 import REVISION, DECISION, route_class and why
    (and DRE-6452 reads UNCERTAIN_GOES_TO as DECISION); DRE-6380 reads
    classify. A rename or signature change here breaks those cards blind."""

    def test_constants_carry_their_stated_values(self):
        assert sbc.REVISION == "revision"
        assert sbc.DECISION == "decision"
        assert sbc.UNCERTAIN_GOES_TO == sbc.DECISION

    @pytest.mark.parametrize("name, ret", [
        ("classify", str | None),
        ("route_class", str),
        ("why", str),
    ])
    def test_function_signatures(self, name, ret):
        sig = inspect.signature(getattr(sbc, name), eval_str=True)
        assert list(sig.parameters) == ["finding"]
        assert sig.parameters["finding"].annotation is str
        assert sig.return_annotation == ret


# --- The vocabulary lives once ------------------------------------------------

class TestVocabularyLivesOnce:
    def test_lists_and_precedence_are_module_data(self):
        for name in ("REVISION_PHRASES", "DECISION_PHRASES", "PROVENANCE_PHRASES"):
            value = getattr(sbc, name)
            assert isinstance(value, tuple) and value, name
        # Precedence is the one place the lists overlap on purpose: every
        # provenance phrase is a revision phrase.
        assert set(sbc.PROVENANCE_PHRASES) <= set(sbc.REVISION_PHRASES)

    def test_critic_phrases_appear_only_in_this_module(self):
        """Each critic-derived phrase is grepped across scripts/. The two
        board phrases are quoted from routing_verdict.py and
        planning_classify.py, which own them, so they are left out. An
        ordered phrase is grepped by its longest part: its opener ("the card
        still") is ordinary English half the scripts use."""
        board = set(sbc.BOARD_QUOTED)
        critic = [p for p in sbc.REVISION_PHRASES + sbc.DECISION_PHRASES
                  if p not in board]
        assert len(critic) >= 10
        for phrase in critic:
            needle = max(phrase, key=len).lower()
            holders = sorted(
                path.name for path in SCRIPTS.rglob("*")
                if path.is_file() and path.suffix in {".py", ".sh", ".md", ".json", ".yml"}
                and needle in path.read_text(encoding="utf-8", errors="replace").lower()
            )
            assert holders == ["send_back_class.py"], (phrase, holders)

    def test_board_phrases_are_quoted_from_their_owners(self):
        owners = {
            "no exit condition to route on": "routing_verdict.py",
            "asks for a decision rather than for work": "planning_classify.py",
        }
        quoted = {p[0] for p in sbc.BOARD_QUOTED}
        assert quoted == set(owners)
        for phrase, owner in owners.items():
            assert phrase in (SCRIPTS / owner).read_text(encoding="utf-8")


# --- The real findings --------------------------------------------------------

def test_fixture_carries_the_eleven_records():
    records = _records()
    assert len(records) == 11
    assert sum(r["card"] == "DRE-3879" for r in records) == 6
    assert sum(r["card"] == "DRE-3880" for r in records) == 3
    assert {r["expected"] for r in records} == {"revision", "decision"}


@pytest.mark.parametrize(
    "record", _records(),
    ids=lambda r: f"{r['card']}-r{r['round']}-{r['expected']}",
)
def test_real_finding_classifies_as_expected(record):
    assert sbc.classify(record["finding"]) == record["expected"]


def test_none_of_the_real_findings_needs_precedence():
    """Each of the nine critic findings matches one list only — so the
    precedence rule is exercised by the constructed finding below, not by
    luck in the fixture."""
    for r in _records():
        rev = sbc.matches(r["finding"], sbc.REVISION_PHRASES)
        dec = sbc.matches(r["finding"], sbc.DECISION_PHRASES)
        assert bool(rev) != bool(dec), (r["card"], r["round"], rev, dec)


def test_ordered_phrase_needs_its_parts_in_order():
    flipped = ("the CEO's newest, verified answer says keep it, but the card "
               "still tells the builder to retire it")
    assert sbc.classify(_finding("DRE-3880", 3)) == sbc.REVISION
    assert sbc.classify(flipped) is None


def test_matching_ignores_case_and_curly_apostrophes():
    curly = "The card doesn’t say which two branches are allowed."
    assert sbc.classify(curly) == sbc.REVISION
    assert sbc.classify("NOBODY HAS MADE THAT CALL.") == sbc.DECISION


# --- Uncertainty goes to the CEO ----------------------------------------------

class TestUncertainty:
    def test_neither_list_is_none_and_routes_to_decision(self):
        assert sbc.classify(NEITHER) is None
        assert sbc.route_class(NEITHER) == sbc.DECISION

    def test_both_lists_outside_precedence_is_none(self):
        both = ("The card doesn't say which fix it wants, and nobody has made "
                "that call.")
        assert sbc.classify(both) is None
        assert sbc.route_class(both) == sbc.DECISION

    def test_reason_sits_beside_the_constant(self):
        lines = MODULE.read_text(encoding="utf-8").splitlines()
        at = [i for i, line in enumerate(lines)
              if line.startswith("UNCERTAIN_GOES_TO")]
        assert len(at) == 1
        window = " ".join(lines[max(0, at[0] - 5): at[0] + 6])
        window = " ".join(window.replace("#", " ").split())
        assert "under-asking a genuine decision is the worse failure" in window

    def test_route_class_passes_a_clear_class_through(self):
        assert sbc.route_class(_finding("DRE-3879", 4)) == sbc.REVISION
        assert sbc.route_class(_finding("DRE-3879", 1)) == sbc.DECISION


# --- Precedence ---------------------------------------------------------------

def test_provenance_outranks_a_decision_phrase():
    assert sbc.matches(BOTH_WITH_PROVENANCE, sbc.DECISION_PHRASES)
    assert sbc.classify(BOTH_WITH_PROVENANCE) == sbc.REVISION
    assert sbc.route_class(BOTH_WITH_PROVENANCE) == sbc.REVISION


# --- why() --------------------------------------------------------------------

class TestWhy:
    def test_names_the_deciding_phrase_for_dre_3879_round_4(self):
        line = sbc.why(_finding("DRE-3879", 4))
        assert "doesn't say which" in line
        assert line.startswith("revision")

    def test_decision_names_its_phrase(self):
        assert "nobody has made that call" in sbc.why(_finding("DRE-3879", 3))

    def test_precedence_names_the_provenance_phrase(self):
        line = sbc.why(BOTH_WITH_PROVENANCE)
        assert "not confirmed to be from the CEO" in line

    def test_uncertain_says_it_goes_to_the_ceo(self):
        line = sbc.why(NEITHER)
        assert line.startswith("uncertain — ")
        assert "CEO" in line

    def test_is_one_line_with_no_tab(self):
        for r in _records():
            line = sbc.why(r["finding"])
            assert "\n" not in line and "\t" not in line


# --- No import of the modules that will import this one -----------------------

def test_imports_neither_plan_critic_nor_planning_route():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "assert 'plan_critic' not in sys.modules\n"
        "assert 'planning_route' not in sys.modules\n"
        "import send_back_class\n"
        "leaked = [m for m in ('plan_critic', 'planning_route') if m in sys.modules]\n"
        "assert not leaked, leaked\n"
        "print('clean')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "clean"


# --- CLI ----------------------------------------------------------------------

def test_cli_classifies_one_finding_per_line():
    findings = [r["finding"] for r in _records()] + [NEITHER]
    out = subprocess.run(
        [sys.executable, str(MODULE), "classify"],
        input="\n".join(findings) + "\n",
        capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0, out.stderr
    rows = out.stdout.splitlines()
    assert len(rows) == len(findings)
    expected = [r["expected"] for r in _records()] + ["uncertain"]
    for row, finding, want in zip(rows, findings, expected):
        cls, reason, echoed = row.split("\t")
        assert cls == want
        assert echoed == finding
        assert reason == sbc.why(finding)
    assert "CEO" in rows[-1].split("\t")[1]


def test_cli_exits_zero_on_empty_input():
    out = subprocess.run(
        [sys.executable, str(MODULE), "classify"],
        input="", capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0
    assert out.stdout == ""
