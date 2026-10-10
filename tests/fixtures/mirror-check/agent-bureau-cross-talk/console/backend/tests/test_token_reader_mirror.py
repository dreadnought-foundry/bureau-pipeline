"""Stand-in for a console test that needs the real module (DRE-6670 fixture).

Its fixture imports `standin_tokens.algorithms`, the stand-in's package under
`console/backend/`. Run in its own process it passes. Run after the relay
stand-in that stubs `standin_tokens`, it errors at setup with `'standin_tokens'
is not a package` — the shape of run 38072065598's `'jwt' is not a package`.

Skipped where no bureau-pipeline checkout is found, which is how it behaves
when bureau-pipeline's own suite collects it from tests/fixtures.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = Path(__file__).resolve().parents[1]
PIPELINE = Path(os.environ.get("BUREAU_PIPELINE_DIR") or ROOT / ".bureau-pipeline")
VERDICTS = PIPELINE / "config" / "routing-verdicts.json"


@pytest.fixture
def algorithms():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import standin_tokens.algorithms

    return standin_tokens.algorithms


@pytest.mark.skipif(not VERDICTS.is_file(), reason="no bureau-pipeline checkout")
def test_the_console_signs_with_the_real_module(algorithms):
    assert algorithms.NAME == "HS256"
