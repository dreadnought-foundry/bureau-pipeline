"""Stand-in for one of agent-bureau's mirror drift tests (DRE-6496 fixture).

agent-bureau keeps its own copy of bureau-pipeline's routing vocabulary and
proves the copy against bureau-pipeline at `stable`, found through
`BUREAU_PIPELINE_DIR` (or the `.bureau-pipeline` checkout beside the repo
root). This file is shaped like the console-side test that went red on
2026-10-09 when the operator-step change reached `stable`: its mirror is the
vocabulary as it stood before that change.

Skipped where no bureau-pipeline checkout is found — which is how it behaves
when bureau-pipeline's own suite collects it from tests/fixtures.
"""

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PIPELINE = Path(os.environ.get("BUREAU_PIPELINE_DIR") or ROOT / ".bureau-pipeline")
VERDICTS = PIPELINE / "config" / "routing-verdicts.json"

#: agent-bureau's mirror — the copy that has to follow bureau-pipeline.
MIRROR = {
    "FLEET": [],
    "WORKBENCH": ["hand-built"],
    "OPERATOR": ["hand-built", "no-code"],
    "PARKED": [],
    "NEEDS WORK": [],
}


@pytest.mark.skipif(not VERDICTS.is_file(), reason="no bureau-pipeline checkout")
def test_the_mirror_matches_bureau_pipeline():
    upstream = json.loads(VERDICTS.read_text())["verdicts"]
    assert {v["name"]: v["marks"] for v in upstream} == MIRROR
