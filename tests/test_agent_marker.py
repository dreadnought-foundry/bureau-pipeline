"""The observability marker: which agent acted, machine-readably (DRE-2727)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import agent_marker  # noqa: E402
import reconcile  # noqa: E402

ENV = {
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "dreadnought-foundry/portico",
    "GITHUB_RUN_ID": "42",
}


class TestTheLine:
    def test_it_names_the_role_and_the_run(self):
        line = agent_marker.actor_line("engineer", env=ENV)
        assert line == (
            "🤖 agent-actor: engineer · run "
            "https://github.com/dreadnought-foundry/portico/actions/runs/42"
        )

    def test_outside_ci_it_omits_the_run_rather_than_inventing_one(self):
        assert agent_marker.actor_line("verifier", env={}) == "🤖 agent-actor: verifier"

    def test_a_hyphenated_role_is_a_role(self):
        assert agent_marker.actor_line("database-architect", env={}).endswith(
            "database-architect"
        )

    def test_it_normalises_case_and_padding(self):
        assert agent_marker.actor_line("  DevOps  ", env={}) == "🤖 agent-actor: devops"

    @pytest.mark.parametrize("bad", ["", "   ", "Engineer Agent", "engineer!", "2fast"])
    def test_a_non_role_is_refused(self, bad):
        # A marker nothing can parse is worse than no marker: it looks like a
        # record and answers nothing.
        with pytest.raises(ValueError):
            agent_marker.actor_line(bad, env={})


class TestReadingItBack:
    def test_the_newest_marker_wins(self):
        bodies = [
            "⏳ 1/5 plan formed",
            agent_marker.actor_line("engineer", env={}),
            "🤖 PR opened: …",
            agent_marker.actor_line("frontend", env={}),
        ]
        assert agent_marker.actor_role(bodies) == "frontend"

    def test_no_marker_reads_as_none(self):
        assert agent_marker.actor_role(["⏳ 1/5 plan formed", "🛑 Agent blocked"]) is None
        assert agent_marker.actor_role([]) is None

    def test_prose_that_merely_mentions_the_marker_is_not_a_record(self):
        # Anchored at the start of the comment, like every other marker the
        # pipeline reads. A quoted marker inside a report is not a record.
        bodies = [f"The brief says to post `{agent_marker.ACTOR_MARKER} engineer`."]
        assert agent_marker.actor_role(bodies) is None


class TestItPlaysWithTheOtherMarkers:
    def test_the_sweep_reads_it_as_a_machine_comment(self):
        # It must never count as the HUMAN reply that clears an agent blocker
        # and re-arms the dispatch loop.
        assert agent_marker.ACTOR_MARKER[0] in reconcile._AGENT_COMMENT_PREFIXES

    def test_it_is_not_proof_of_life(self):
        # A receipt that an agent acted is not evidence a run is still alive;
        # counting it as such would suppress the dead-run requeue.
        assert agent_marker.ACTOR_MARKER[0] not in reconcile._LIFE_PREFIXES
