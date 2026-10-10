"""Stand-in for the relay tests that stub a module at import (DRE-6670 fixture).

Three of agent-bureau's relay test files run `sys.modules.setdefault("jwt",
mock.MagicMock())` at import. Run in one pytest process with the console's
tests, the stub went in first and every console test that needs the real
module errored at setup — 1238 of them on Promote Channel run 38072065598.

This file does the same to `standin_tokens`, a package that exists only inside
the stand-in (`console/backend/standin_tokens/`), and only when it is run as
agent-bureau: a bureau-pipeline checkout found the way the other stand-ins find
it. Collected by bureau-pipeline's own suite it finds none, stubs nothing and
skips.
"""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = Path(os.environ.get("BUREAU_PIPELINE_DIR") or ROOT / ".bureau-pipeline")
VERDICTS = PIPELINE / "config" / "routing-verdicts.json"

if VERDICTS.is_file():
    sys.modules.setdefault("standin_tokens", mock.MagicMock())


@pytest.mark.skipif(not VERDICTS.is_file(), reason="no bureau-pipeline checkout")
def test_the_relay_reads_the_vocabulary():
    assert VERDICTS.read_text().strip()
