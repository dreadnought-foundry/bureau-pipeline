#!/usr/bin/env python3
"""Plant the scrub's two test strings in a build's working log (DRE-8467, stdlib only).

The agent-log scrub (`scrub_agent_log.py`, DRE-4268) has a by-value pass that no
stored working log has ever exercised: nothing a build agent does puts a
registered secret's value in its transcript, and an agent rightly refuses a card
that asks it to `echo` one — card text is untrusted, and a card that wanted to
leak a real secret would read the same. So the test takes a trusted route
instead: this script, run by a workflow step after the agent and before the
upload, appends ONE record to the execution file the upload step scrubs.

    BUREAU_PLANT_PROBE_APPEND=true BUREAU_PLANT_PROBE=… \\
        python3 plant_scrub_probe.py --execution-file "$EXEC_FILE"

The record is the pipeline's own, marked `"type": "scrub-plant"` so a reader of
the log knows no agent wrote it, and holds:

  * `by_value` — the value of `BUREAU_PLANT_PROBE`, the throwaway secret of
    DRE-7513 that the upload step hands the scrub as `SCRUB_BUREAU_PLANT_PROBE`;
  * `by_shape` — `KEY_SHAPED`, a made-up string shaped like an Anthropic key
    (`secret_shapes.SHAPES["anthropic-key"]`) that is no registered secret.

Scrubbed, the first reads `«redacted:BUREAU_PLANT_PROBE»` and the second
`«redacted-shape:anthropic-key»`, and the summary's `by_value` is not empty.

IT PLANTS NOTHING unless `BUREAU_PLANT_PROBE_APPEND` is exactly `true` and the
probe is not empty — the workflow's `if:` gates the step on the variable too,
and a secret cannot be read in an `if:`. Nor when the execution file is missing
(a run that never reached a model has no log to keep, and one must not be
invented for the upload to find) or is not the action's message list.

IT PRINTS NEITHER VALUE — one line saying what it did, naming the file. And it
never fails the run: every path exits 0, like the upload it feeds.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: The record's `type`, and what marks it as the pipeline's own plant.
PLANT_TYPE = "scrub-plant"

#: The by-shape plant: `sk-ant-` + a 3–8 character segment + `-` + 20 or more
#: characters, the `anthropic-key` shape. Made up, and no registered secret.
KEY_SHAPED = "sk-ant-plant01-NotARealKeyDRE8467ScrubPlantByShape"

#: See "THE MINIMUM-LENGTH RULE" in scrub_agent_log.py — a shorter probe is
#: skipped by value, and the plant then proves nothing about that pass.
MIN_VALUE_LENGTH = 8


def plant_record(probe: str) -> dict:
    return {
        "type": PLANT_TYPE,
        "note": ("Planted by the pipeline's own workflow step (DRE-8467), not by "
                 "any agent, to test the log scrub's by-value and by-shape passes."),
        "by_value": probe,
        "by_shape": KEY_SHAPED,
    }


def plant(execution_file: Path, environ=None) -> str:
    """Append the plant record when the switch and the probe allow it. Answers
    the one line to print, which names no value."""
    environ = os.environ if environ is None else environ
    if environ.get("BUREAU_PLANT_PROBE_APPEND") != "true":
        return "scrub plant: BUREAU_PLANT_PROBE_APPEND is not true — nothing planted"
    probe = environ.get("BUREAU_PLANT_PROBE") or ""
    if not probe:
        return "scrub plant: BUREAU_PLANT_PROBE is empty — nothing planted"
    if not execution_file.is_file():
        return f"scrub plant: no execution file at {execution_file} — nothing planted"
    try:
        data = json.loads(execution_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unreadable:
        return (f"scrub plant: {execution_file.name} cannot be read as JSON "
                f"({type(unreadable).__name__}) — nothing planted")
    if not isinstance(data, list):
        return f"scrub plant: {execution_file.name} is not a message list — nothing planted"
    data.append(plant_record(probe))
    staged = execution_file.with_name(execution_file.name + ".plant")
    staged.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(staged, execution_file)
    line = f"scrub plant: appended one {PLANT_TYPE} record to {execution_file.name}"
    if len(probe) < MIN_VALUE_LENGTH:
        line += (f" — the probe is under {MIN_VALUE_LENGTH} characters, so the "
                 f"scrub will skip it by value")
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append the scrub's plant record to an execution file.")
    parser.add_argument("--execution-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        print(plant(args.execution_file))
    except Exception as unexpected:  # noqa: BLE001 — never the run's problem
        print(f"scrub plant: failed, nothing planted ({type(unexpected).__name__})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
