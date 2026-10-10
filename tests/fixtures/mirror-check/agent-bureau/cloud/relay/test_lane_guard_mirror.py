"""Stand-in for the relay's routing-verdict drift test (DRE-6496 fixture).

The relay carries its own copy of which marks each verdict stamps, and on
2026-10-09 that copy was also live behavior in the deployed relay. It reads
bureau-pipeline at `.bureau-pipeline` under the repo root unless
`BUREAU_PIPELINE_DIR` names another checkout.
"""

import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE = os.environ.get("BUREAU_PIPELINE_DIR") or os.path.join(ROOT, ".bureau-pipeline")
VERDICTS = os.path.join(PIPELINE, "config/routing-verdicts.json")

#: The relay's copy: the verdicts whose cards it treats as a person's.
HAND_MARKS = {"WORKBENCH": ("hand-built",), "OPERATOR": ("hand-built", "no-code")}


@pytest.mark.skipif(not os.path.isfile(VERDICTS), reason="no bureau-pipeline checkout")
def test_the_relays_hand_marks_follow_the_vocabulary():
    with open(VERDICTS) as fh:
        upstream = {v["name"]: tuple(v["marks"]) for v in json.load(fh)["verdicts"]}
    for name, marks in HAND_MARKS.items():
        assert upstream[name] == marks, name
