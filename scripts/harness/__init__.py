"""Integration harness for the live pipeline (DRE-2098).

Drives end-to-end scenarios against the dedicated sandbox repo
(dreadnought-foundry/bureau-harness) with REAL GitHub behavior — real
branches, real PRs, real critic runs, real merges. Nothing GitHub-side is
mocked; the unit tests cover only the driver's pure logic.

Run via .github/workflows/harness.yml (workflow_dispatch) or locally:

    HARNESS_WORKER_TOKEN=... PYTHONPATH=scripts \
        python3 -m harness --repo dreadnought-foundry/bureau-harness

See scripts/harness/README.md for the scenario contract and the
Linear-side design decision.
"""

import os
import sys

# The flat pipeline scripts (merge_gate, should_review_pr, …) live one
# directory up and import as top-level modules — same path bootstrap the
# scripts themselves use (reconcile.py et al.). The harness REUSES
# merge_gate's verdict parsing so its idea of "a verdict bound to the head
# sha" can never drift from the real gate's.
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
