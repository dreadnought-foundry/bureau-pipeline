"""No live file carries a retired wave marker (DRE-4718).

The wave route is gone: DRE-4699 stopped the vocabulary stamping `wave`,
DRE-4700 deleted `wave_commitment.py`, `wave_plan.py` and the wave-plan
standard, and DRE-4718 replaced `plan.yml`'s wave branch with the roll-up
route. A marker left behind in a workflow, a script, a standard, a brief or a
config file is a call into a module that no longer exists, a gate on a shape
the classifier cannot produce, or a rule nothing enforces any more — dead code
that reads as live.

So this greps the five directories that ARE the pipeline and fails naming every
file that still carries one. `docs/` and `tests/` are history and are not
scanned. The word "wave" alone is not a marker — `Wave 1.5` names a programme.

No file under `config/` is skipped any more. The one that was — the committed
split ledger, derived from the files each past split piece's merged pull
requests touched, which listed a wave module because a merged pull request
edited it — is no longer committed (DRE-6056): the plan job derives the ledger
into its runner's temp directory at the start of each run.

Run: python3 -m pytest tests/test_no_wave_markers.py -v
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The directories that are the live pipeline.
SCANNED = (".github/workflows", "scripts", "standards", "briefs", "config")

#: The retired markers, each one naming something that no longer exists.
MARKERS = (
    "wave_commitment",
    "wave_plan",
    "committed-in-sequence",
    "wave-commitment",
    "standards/wave-plan.md",
    "route == 'wave'",
)

#: Derived history, not live pipeline — see the module docstring. Empty since
#: DRE-6056; a new entry should be argued for, not added to make a test pass.
HISTORY: frozenset = frozenset()


def _files():
    for top in SCANNED:
        for path in sorted((ROOT / top).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel in HISTORY:
                continue
            yield rel, path


def carriers() -> dict:
    """Every scanned file that carries a marker, and which markers it carries."""
    found = {}
    for rel, path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits = [m for m in MARKERS if m in text]
        if hits:
            found[rel] = hits
    return found


def test_no_live_file_carries_a_retired_wave_marker():
    found = carriers()
    assert not found, "retired wave markers are still live in: " + "; ".join(
        f"{rel} ({', '.join(hits)})" for rel, hits in sorted(found.items()))


def test_the_scan_reads_the_files_it_claims_to():
    """A guard that scanned nothing would pass forever. The workflow the wave
    branch lived in, and a script, must both be read."""
    scanned = {rel for rel, _ in _files()}
    assert ".github/workflows/plan.yml" in scanned
    assert "scripts/epic_split.py" in scanned
    assert "standards/card-quality.md" in scanned


def test_the_history_exemption_is_empty():
    """The derived ledger it held is no longer committed (DRE-6056), so every
    file under the scanned directories is read."""
    assert HISTORY == frozenset()


def test_a_bare_wave_is_not_a_marker():
    """`Wave 1.5` names a programme; only the retired names are markers."""
    assert not any(m in "Wave 1.5 of the console programme" for m in MARKERS)
