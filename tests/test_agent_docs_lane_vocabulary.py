"""Every brief and every standard names only lanes that exist (DRE-2727).

The briefs and the standards ARE the agents' operating instructions. Change the
lanes without changing what the agents are told and every agent keeps operating
the old model — confidently, with no error to notice. That is the
producer/consumer drift `standards/engineering.md` already names, and a document
is the one consumer nothing else checks.

RETIRED_LANES is spelled out HERE rather than derived from the contract, for the
reason tests/test_lane_contract.py gives for doing the same: the contract no
longer spells the retired names anywhere, so a scan keyed off its own lane list
could not find a name it has forgotten, and a vacuous green is how the next
cleanup gets missed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config" / "lane-contract.json"
BRIEFS = sorted((ROOT / "briefs").glob("*.md"))
STANDARDS = sorted((ROOT / "standards").glob("*.md"))
AGENT_DOCS = BRIEFS + STANDARDS

# The states DRE-2726 folded and DRE-2818 deleted from the board AND the
# contract. No document may still name one.
RETIRED_LANES = ("In Design Review", "In QA")


def _contract() -> dict:
    with open(CONTRACT, encoding="utf-8") as fh:
        return json.load(fh)


def live_lanes() -> list[str]:
    return [l["name"] for l in _contract()["lanes"] if l.get("status") == "live"]


def alias_names() -> list[str]:
    return [e["from"] for e in _contract()["aliases"]["entries"]]


def lane_writing_briefs() -> list[Path]:
    """Briefs whose agent can actually move a card between lanes.

    A lane move is a Linear write, so the roster's own `credentials` list
    answers it: an agent handed no LINEAR_API_KEY cannot make one however
    thoroughly it is taught the vocabulary. Read off agents.yaml rather than
    written down here, so a brief joins or leaves this population when its
    agent's credentials change and not when someone remembers to edit a list.
    """
    import yaml

    with open(ROOT / "agents.yaml", encoding="utf-8") as fh:
        roster = yaml.safe_load(fh)["agents"]
    briefs = [
        ROOT / a["briefPath"]
        for a in roster
        if a.get("briefPath") and "LINEAR_API_KEY" in (a.get("credentials") or [])
    ]
    assert briefs, "no briefed agent holds a Linear key — the scan is vacuous"
    return briefs


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestNoRetiredLaneSurvivesInAnAgentDocument:
    def test_no_brief_or_standard_names_a_retired_lane(self):
        offenders = []
        for doc in AGENT_DOCS:
            body = _text(doc)
            for name in RETIRED_LANES:
                if re.search(rf"\b{re.escape(name)}\b", body):
                    offenders.append(f"{doc.relative_to(ROOT)} names {name!r}")
        assert not offenders, (
            "retired lane names still in the agents' operating instructions: "
            + "; ".join(offenders)
        )

    def test_no_brief_or_standard_names_a_retired_alias(self):
        # An alias is accepted on INPUT so a board mid-rename still resolves.
        # It is never a lane, and an instruction that tells an agent to move a
        # card to one is telling it to use a name the board no longer shows.
        offenders = []
        for doc in AGENT_DOCS:
            body = _text(doc)
            for name in alias_names():
                if re.search(rf"\b{re.escape(name)}\b", body):
                    offenders.append(f"{doc.relative_to(ROOT)} names alias {name!r}")
        assert not offenders, "; ".join(offenders)


class TestTheLiveVocabularyIsActuallyTaught:
    def test_the_build_briefs_name_the_new_lane_vocabulary(self):
        # Intake and Green Light are the two lanes the wave introduced, and
        # Triage's job changed under them. An agent that has never been told
        # they exist routes a card to the lane it knew.
        #
        # The population is every brief whose agent can actually WRITE a lane,
        # which is a question the roster already answers: a lane move goes
        # through Linear, so an agent with no LINEAR_API_KEY cannot make one.
        # DERIVED, never listed (DRE-3084 gave the critic a brief and the
        # critic is denied Linear on purpose) — a hardcoded exemption is how
        # the next brief joins the population by nobody's decision.
        required = ("Intake", "Green Light", "Triage")
        for brief in lane_writing_briefs():
            body = _text(brief)
            missing = [n for n in required if n not in body]
            assert not missing, f"{brief.relative_to(ROOT)} never names {missing}"

    def test_the_architecture_standard_names_every_live_lane(self):
        body = _text(ROOT / "standards" / "architecture.md")
        missing = [n for n in live_lanes() if not re.search(rf"\b{re.escape(n)}\b", body)]
        assert not missing, (
            "standards/architecture.md carries the canonical flow but never "
            f"names {missing}"
        )
