"""Scenario discovery — convention-based, never a shared registry.

Each module in this package exposes a module-level `SCENARIO` (a
framework.Scenario with a unique `name`). Sibling cards add scenarios as
NEW FILES only (dependabot_flow, gate_paths), so parallel PRs never fight
over a registry edit (standards/engineering.md, "Don't fight over shared
files").
"""

from __future__ import annotations

import importlib
import pkgutil


def discover() -> dict:
    """name → scenario instance, for every module here that exports one."""
    found = {}
    for mod_info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{mod_info.name}")
        scenario = getattr(module, "SCENARIO", None)
        if scenario is not None and getattr(scenario, "name", ""):
            found[scenario.name] = scenario
    return found
