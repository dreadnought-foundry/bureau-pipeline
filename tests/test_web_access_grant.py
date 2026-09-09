"""RED-first tests for DRE-2785 — every fleet agent can reach the web.

THE GAP. The operator authorised web access on 2026-08-26 ("all of these need
to have access to the web to go look up best practices and really understand
where the state of the art is or if something has changed. Be able to fetch
too."). DRE-2712 closed with no linked pull request and the grants were never
made: read live on 2026-08-27, `agents.yaml` carried ZERO occurrences of
`WebSearch` or `WebFetch`, and every workflow's `--allowedTools` still granted
the six local tools. Every agent in the fleet reasoned about the outside world
from training data alone.

WHAT THIS FILE PINS, and why each half is here:

1. THE GRANT ITSELF, in both places it has to be true. `agents.yaml` is the
   console's machine-readable answer to "what can this agent do"; the
   workflow's `--allowedTools` is what the agent actually gets. Either one
   alone is a claim; tests/test_agents_registry.py already fails on drift
   BETWEEN them, so this file asserts the CONTENT both must carry.

2. ALL OF THEM, IN ONE CHANGE. The card settles the staging question and
   this is the mechanical form of that settlement: a writer that can make
   unverifiable external claims, reviewed by a critic that cannot check
   them, is worse than neither. A per-agent allowlist here would let the
   next entry be added without the grant and nothing would say so.

3. THE TURN BUDGETS, RAISED WITH THE GRANT. Search costs turns, and every
   ceiling in this fleet was sized by measuring runs that could not search.
   A crashed critic writes no verdict, reconcile retries a bounded number of
   times, and the pull request simply sits — so the ceilings move WITH the
   capability rather than after the first death. The pre-grant numbers are
   pinned below so "raised" is checked against what was actually there.

4. THE REASON, RECORDED PER AGENT. `agents.yaml` is where the fleet's
   budgets are explained to the next reader; a number that moved with no
   note is how the roster came to say 80 for a planner running at 120.

5. THE STANDARD NAMES THE WEB. `standards/untrusted-content.md` was written
   when nothing could reach further than a Linear comment. A fetched page is
   a far more hostile surface: anyone can author one, and the agent chose to
   go there.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
sys.path.insert(0, str(REPO / "scripts"))

import plan_critic as pc  # noqa: E402
import pr_size_strategy as pss  # noqa: E402

ACTION = "anthropics/claude-code-action"

#: The two tools the operator asked for, by their Claude Code names. Search to
#: find out whether something has changed; fetch to read the page it found.
WEB_TOOLS = ("WebSearch", "WebFetch")

_TOOLS_RE = re.compile(r'--allowedTools\s+"([^"]*)"')
_TURNS_RE = re.compile(r"--max-turns\s+(\d+)")


def roster() -> list[dict]:
    return yaml.safe_load((REPO / "agents.yaml").read_text())["agents"]


def entry(name: str) -> dict:
    for a in roster():
        if a["name"] == name:
            return a
    raise AssertionError(f"no {name!r} entry in agents.yaml")


def comment_paragraphs(block: str) -> list[str]:
    """The contiguous runs of `#` comment lines in one roster entry.

    A paragraph, not a line, because that is the unit a reason is written in —
    and not the whole block, because `maxTurns:` is itself a key containing the
    word "turn", which would make any keyword assertion over the raw text pass
    for free.
    """
    paras, current = [], []
    for line in block.splitlines():
        if line.strip().startswith("#"):
            current.append(line.strip().lstrip("#").strip())
        elif current:
            paras.append(" ".join(current))
            current = []
    if current:
        paras.append(" ".join(current))
    return paras


def entry_blocks() -> dict[str, str]:
    """The RAW yaml text of each roster entry, comments and all.

    Parsed YAML drops the comments, and the comments are where the reason for
    a budget lives — so the "record the reason" assertion has to read the file
    as text rather than as data.
    """
    raw = (REPO / "agents.yaml").read_text()
    body = raw.split("\nagents:", 1)[1]
    blocks, name = {}, None
    for chunk in re.split(r"(?m)^(?=  - name: )", body):
        m = re.search(r"(?m)^  - name: (\S+)", chunk)
        if m:
            name = m.group(1)
            blocks[name] = chunk
        elif name and chunk.strip():
            blocks[name] += chunk
    return blocks


def agent_steps(path: Path) -> dict[str, dict]:
    """Every claude-code-action step in a workflow, keyed by step id."""
    doc = yaml.safe_load(path.read_text())
    steps = {}
    for job in (doc.get("jobs") or {}).values():
        for i, step in enumerate(job.get("steps") or []):
            if str(step.get("uses") or "").split("@")[0] == ACTION:
                steps[step.get("id") or f"steps[{i}]"] = step
    return steps


def workflow_paths() -> list[Path]:
    return sorted({REPO / a["workflow"] for a in roster()})


def ceiling(workflow: str, step_id: str) -> int:
    args = str((agent_steps(WORKFLOWS / workflow)[step_id].get("with") or {})
               .get("claude_args") or "")
    m = _TURNS_RE.search(args)
    assert m, f"{workflow}:{step_id} declares no literal --max-turns: {args!r}"
    return int(m.group(1))


class TestEveryAgentDeclaresTheWebTools:
    """Half one of the grant: the roster the console reads."""

    @pytest.mark.parametrize("name", sorted(a["name"] for a in roster()))
    @pytest.mark.parametrize("tool", WEB_TOOLS)
    def test_the_roster_entry_grants_it(self, name, tool):
        tools = entry(name)["tools"]
        assert tool in tools, (
            f"{name}: agents.yaml declares {sorted(tools)} — no {tool}. The "
            f"staging question is settled (DRE-2785): all of them get it in "
            f"one change, because a writer that can make unverifiable "
            f"external claims reviewed by a critic that cannot check them is "
            f"worse than neither"
        )

    def test_no_entry_is_left_behind(self):
        """The assertion the parametrised one cannot make: it is EVERY entry,
        so an agent added later without the grant fails here."""
        missing = sorted(a["name"] for a in roster()
                         if not set(WEB_TOOLS) <= set(a["tools"]))
        assert missing == [], (
            f"these roster entries cannot reach the web: {missing}"
        )


class TestEveryWorkflowGrantsTheWebTools:
    """Half two: what the agent is actually handed at run time.

    tests/test_agents_registry.py fails when the roster and the workflow
    DISAGREE. Both being wrong in the same way is exactly what that check
    cannot see — which is the state the fleet was in.
    """

    @pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
    def test_every_invocation_in_the_file_grants_them(self, path):
        found = _TOOLS_RE.findall(path.read_text())
        assert found, f"{path.name} declares no --allowedTools at all"
        for granted in found:
            passed = {t.strip() for t in granted.split(",") if t.strip()}
            assert set(WEB_TOOLS) <= passed, (
                f"{path.name} runs an agent with {sorted(passed)} — the web "
                f"tools are not among them"
            )

    def test_the_harness_mirrors_the_workflow_it_claims_to_mirror(self):
        """scripts/harness/agent_run.py runs a REAL build agent on the SHIPPED
        prompt, and its own comment says it carries agent-task.yml's tool
        surface. A harness granting less than production proves the wrong
        agent obeys the gate."""
        import harness.agent_run as ar  # noqa: PLC0415 — heavy package import

        shipped = _TOOLS_RE.findall((WORKFLOWS / "agent-task.yml").read_text())
        assert len(shipped) == 1, (
            f"agent-task.yml now has {len(shipped)} tool grants; the harness "
            f"mirror below can no longer name one of them"
        )
        assert {t.strip() for t in ar.ALLOWED_TOOLS.split(",")} == \
            {t.strip() for t in shipped[0].split(",")}, (
                f"the harness grants {ar.ALLOWED_TOOLS!r} and agent-task.yml "
                f"grants {shipped[0]!r} — the harness's own docstring says it "
                f"mirrors the workflow's claude_args"
            )


class TestTheTurnBudgetsRoseWithTheGrant:
    """Search costs turns. Each number below is what the agent ran with
    BEFORE the grant, read off `main` on 2026-09-09; the assertion is that it
    moved, not that it reached some particular new value."""

    #: agents.yaml's `maxTurns` before DRE-2785, per agent.
    BEFORE = {
        "critic": 80,
        "medic": 40,
        "plan-critic-pre": 40,
        "plan-critic-post": 40,
    }

    @pytest.mark.parametrize("name", sorted(BEFORE))
    def test_the_roster_budget_rose(self, name):
        before, now = self.BEFORE[name], entry(name)["maxTurns"]
        assert now > before, (
            f"{name}: maxTurns is still {now}. DRE-2785 grants it web search "
            f"and raises the ceiling in the same change, rather than "
            f"discovering it afterwards on a run that produces nothing"
        )

    def test_the_planner_budget_rose(self):
        """Read separately because the roster was WRONG about it: DRE-3450
        raised the epic-planning step 80 → 120 hours before this card and left
        `maxTurns: 80` behind, which happened to match the wave route's
        literal, so nothing failed. The comparison is against the ceiling the
        step actually ran with."""
        assert entry("planner")["maxTurns"] > 120, (
            f"the planner declares maxTurns {entry('planner')['maxTurns']}; "
            f"plan.yml's `Plan epic` step already ran at 120 before this card"
        )
        assert entry("planner")["maxTurns"] == ceiling("plan.yml", "claude"), (
            "the roster's planner budget is not the ceiling the epic-planning "
            "step runs with — the drift DRE-3450 left behind"
        )

    def test_the_qa_critic_standard_review_rose(self):
        """The critic's ceiling is not a literal in the YAML — qa-review.yml
        sizes the diff and reads the budget out of pr_size_strategy. The
        standard strategy is the path a review normally takes."""
        first, retry = pss.TURN_BUDGET["standard"]
        assert (first, retry) > (80, 120), (
            f"the standard review still runs at {first}/{retry} turns"
        )
        assert retry > first, "a retry must come back with more turns (DRE-2422)"

    def test_the_medic_ceiling_rose_in_its_workflow(self):
        """medic.yml runs one agent and its step carries no id, so the
        ceiling is read as the file's single literal."""
        found = _TURNS_RE.findall((WORKFLOWS / "medic.yml").read_text())
        assert len(found) == 1, f"medic.yml declares {found} ceilings"
        assert int(found[0]) > 40, "the diagnosis agent still runs at 40 turns"

    def test_both_plan_critic_rounds_rose_in_the_workflow(self):
        for step_id in ("prea", "preb"):
            assert ceiling("plan.yml", step_id) > 40, (
                f"plan.yml:{step_id} still runs at 40 turns"
            )

    def test_the_post_approval_review_floor_and_cap_rose(self):
        """The second plan critic's ceiling is sized per plan
        (`plan_critic.post_review_turns`), so raising it means moving the
        band, not a literal."""
        assert pc.POST_REVIEW_TURNS_FLOOR > 40
        assert pc.POST_REVIEW_TURNS_CAP > 120
        assert pc.POST_REVIEW_TURNS_DEFAULT == pc.post_review_turns(15), (
            "the default is the fifteen-card number and must stay derived "
            "from the sizing rather than drifting into a second constant"
        )
        assert entry("plan-critic-post")["maxTurns"] == \
            pc.POST_REVIEW_TURNS_FLOOR, (
                "the roster reports the floor for the post-approval review — "
                "the smallest budget it can be sized to"
            )

    def test_every_ceiling_is_still_a_ceiling(self):
        """Unbounded is not the fix: a looping agent with a search tool is its
        own outage (the 2026-08-09 fleet-down lesson)."""
        for path in workflow_paths():
            for value in _TURNS_RE.findall(path.read_text()):
                assert 0 < int(value) <= 400, (
                    f"{path.name} declares --max-turns {value}"
                )


class TestTheReasonIsRecordedPerAgent:
    """agents.yaml is where the fleet's budgets are explained to the next
    reader. A number that moved with no note is how the roster came to say 80
    for a planner that had been running at 120 since that morning."""

    @staticmethod
    def notes(name: str) -> list[str]:
        """The comment paragraphs in this entry that name the card."""
        return [p for p in comment_paragraphs(entry_blocks()[name])
                if "DRE-2785" in p]

    @pytest.mark.parametrize("name", sorted(a["name"] for a in roster()))
    def test_the_entry_records_the_card_that_granted_the_web(self, name):
        """A capability that arrived with no note is a capability nobody can
        review later."""
        assert self.notes(name), (
            f"{name}: no comment in its agents.yaml block records why it can "
            f"now reach the web"
        )

    @pytest.mark.parametrize("name", sorted(a["name"] for a in roster()))
    def test_the_entry_records_what_happened_to_its_turn_budget(self, name):
        """Recorded PER AGENT, and an unchanged number is a decision too — the
        reader must not have to guess whether it was considered.

        Read off the comment paragraphs that name the card, never the block:
        `maxTurns:` is a key containing the word "turn", so a keyword scan over
        the raw entry would pass for every agent for free.
        """
        assert any(re.search(r"(?i)budget|turn|ceiling|rung", p)
                   for p in self.notes(name)), (
            f"{name}: gains the web tools and its note says nothing about the "
            f"turn budget — raised, or deliberately left where it was"
        )


class TestTheStandardNamesTheWeb:
    """`standards/untrusted-content.md` covered card text, comments and PR
    bodies — everything written when nothing could reach further."""

    @property
    def text(self) -> str:
        return (REPO / "standards" / "untrusted-content.md").read_text()

    def test_it_names_fetched_web_content(self):
        assert re.search(r"(?i)fetched web content", self.text), (
            "the untrusted-content standard does not name fetched web "
            "content; it was written when nothing could reach further than a "
            "Linear comment"
        )

    @pytest.mark.parametrize("tool", WEB_TOOLS)
    def test_it_names_the_tools_by_the_name_the_agent_sees(self, tool):
        """An agent looking for the rule that governs `WebFetch` must find it
        under that word. 'The web' in prose is not searchable from inside a
        run."""
        assert tool in self.text, (
            f"the untrusted-content standard never says {tool}"
        )

    def test_it_says_a_page_cannot_instruct_the_agent(self):
        assert re.search(r"(?i)never an instruction to\s+follow", self.text), (
            "the standard must say plainly that a fetched page is material to "
            "reason about, never an instruction to follow"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
