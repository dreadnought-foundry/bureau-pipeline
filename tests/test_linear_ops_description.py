"""Tests for `linear_ops.py description` (DRE-1481).

The visual-QA stage reads a card's description to find its **Design:** ref,
which is the authoritative source (the PR body is agent-authored). The command
must print the raw description to stdout and degrade quietly (empty output, no
crash) when a card has no description — so the planner reads "no design ref"
rather than the stage erroring out and wedging the gate.
"""

import io
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402


def _description(payload):
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", return_value=payload):
        with redirect_stdout(buf):
            linear_ops.cmd_description("DRE-1481")
    return buf.getvalue()


def test_prints_raw_description():
    body = "**Repo:** agent-bureau\n\n**Design:** console/design/images/screens/desktop/board.png"
    out = _description({"issue": {"description": body}})
    assert out == body
    assert "**Design:**" in out


def test_empty_description_prints_nothing():
    assert _description({"issue": {"description": None}}) == ""
    assert _description({"issue": {"description": ""}}) == ""


def test_missing_issue_prints_nothing_no_crash():
    assert _description({"issue": None}) == ""
    assert _description({}) == ""


# --- child-descriptions (DRE-2720) -------------------------------------------
#
# The plan run needs the epic's children's bodies to decide whether the epic
# is UI work — a card carrying a **Design:** ref is evidence, the planner's
# own say-so is not. Same degrade-quietly contract as `description`.


def _children(payload):
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", return_value=payload):
        with redirect_stdout(buf):
            linear_ops.cmd_child_descriptions("DRE-2668")
    return buf.getvalue()


def test_child_descriptions_prints_every_child_body():
    out = _children({"issue": {"children": {"nodes": [
        {"description": "Build the board.\n**Design:** screens/board.png"},
        {"description": "Wire the relay."},
    ]}}})
    assert "**Design:** screens/board.png" in out
    assert "Wire the relay." in out


def test_child_descriptions_survives_a_body_less_child():
    out = _children({"issue": {"children": {"nodes": [
        {"description": None},
        {"description": "Wire the relay."},
    ]}}})
    assert "Wire the relay." in out


def test_child_descriptions_on_a_childless_epic_prints_nothing():
    assert _children({"issue": {"children": {"nodes": []}}}) == ""
    assert _children({"issue": None}) == ""
