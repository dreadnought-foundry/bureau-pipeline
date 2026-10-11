"""The committed `config/models.yaml` with its project exceptions taken out, or
its session-run agents put back in no group.

The console writes per-project exceptions under the top-level `projects:` block
(DRE-6624), so the live file carries whatever the CEO last saved there. A test
that pins how an edit behaves from NO exceptions starts from this text rather
than assuming the committed block is `projects: {}`.

The same holds for the agents that run in a person's session (DRE-6889): the
CEO moves them into groups and adds new ones from the console, so the committed
file has them wherever he last put them. A test that pins how a move behaves
from an agent in NO group starts from `with_session_agents_ungrouped`, and a
test that adds an agent takes its name from `fresh_agent_name`.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config" / "models.yaml"

# The block runs from `projects:` to the blank line that follows it.
_PROJECTS = re.compile(r"^projects:(?: \{\}\n|\n(?:  .*\n)*)", re.M)


def without_projects(text: str) -> str:
    """`text` with its `projects:` block reset to `projects: {}`."""
    out, count = _PROJECTS.subn("projects: {}\n", text)
    assert count == 1, f"expected one top-level projects: block, found {count}"
    return out


def config_without_projects() -> str:
    return without_projects(CONFIG.read_text())


def _block(lines: list[str], key: str) -> tuple[int, int]:
    """`(start, end)` of the top-level `key:` block: its header line, and the
    first line after it that is not indented — a blank line ends it."""
    start = next(i for i, line in enumerate(lines)
                 if re.match(rf"{re.escape(key)}:(\s|$)", line))
    end = start + 1
    while end < len(lines) and lines[end][:1] == " ":
        end += 1
    return start, end


def with_session_agents_ungrouped(text: str) -> str:
    """`text` with every name under `session_agents` taken out of `agents` and
    listed under `ungrouped`, in `session_agents` order — where the file stood
    before the CEO moved any of them. Every other line is unchanged."""
    session = yaml.safe_load(text).get("session_agents") or []
    lines = text.splitlines(keepends=True)
    start, end = _block(lines, "agents")
    row = re.compile(r"\s+(['\"]?)([^'\"\s:#]+)\1:")
    lines[start + 1:end] = [
        line for line in lines[start + 1:end]
        if not ((m := row.match(line)) and m.group(2) in session)]
    start, end = _block(lines, "ungrouped")
    kept = [m.group(2) for line in lines[start + 1:end]
            if (m := re.match(r"\s+-\s+(['\"]?)([^'\"\s#]+)\1", line))
            and m.group(2) not in session]
    lines[start:end] = (["ungrouped:\n"]
                        + [f"  - {name}\n" for name in kept + list(session)])
    return "".join(lines)


def config_with_session_agents_ungrouped() -> str:
    return with_session_agents_ungrouped(CONFIG.read_text())


def fresh_agent_name(raw: dict, wanted: str = "data-analyst") -> str:
    """`wanted`, or `wanted-2`, `wanted-3`, … — the first that names no agent
    in `raw`, so `add_agent` takes it whatever the CEO has added since."""
    taken = (set(raw.get("agents") or {}) | set(raw.get("ungrouped") or [])
             | set(raw.get("session_agents") or []) | set(raw.get("about") or {}))
    name, n = wanted, 1
    while name in taken:
        n += 1
        name = f"{wanted}-{n}"
    return name
