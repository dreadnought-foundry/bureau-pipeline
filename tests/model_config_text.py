"""The committed `config/models.yaml` with its project exceptions taken out.

The console writes per-project exceptions under the top-level `projects:` block
(DRE-6624), so the live file carries whatever the CEO last saved there. A test
that pins how an edit behaves from NO exceptions starts from this text rather
than assuming the committed block is `projects: {}`.
"""
from __future__ import annotations

import re
from pathlib import Path

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
