"""RED-first: the DRE-6673 replay — the six shapes a name reached this public repository in, each redacted and each refused (DRE-8166).

DRE-6673's proof record carried a person's name six ways. Each shape is held here with synthetic names (every one
carries the marker `synth`), and each must be:

  1. redacted by `name_redact.redact` to its role, and nothing else in the line touched;
  2. refused by `name_redact.findings` unredacted, naming the file, the line and the form's kind;
  3. refused by the live guard on a push of the unredacted line, and passed on a push of the redacted one.

Run: cd bureau-pipeline && python3 -m pytest tests/test_name_redact_replay.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import name_redact  # noqa: E402
from test_name_redact import (  # noqa: E402
    LIVE, OWNER, ZERO, commit, git, make_repo, no_secret, roster, run,
)

#: (shape, unredacted line, redacted line, kind of the form that remains)
SHAPES = [
    ("table cell",
     "| Proof record | written by Synthia Synthwood |",
     "| Proof record | written by [a person] |", "full-name"),
    ("hand move",
     "a person (Synthia Synthwood), by hand from In Review",
     "a person ([a person]), by hand from In Review", "full-name"),
    ("creator cell",
     "| Creator | Synthora Synthbridge |",
     "| Creator | [the CEO] |", "full-name"),
    ("console answer",
     "> Answer from Synthora (signed in to the console)",
     "> Answer from [the CEO] (signed in to the console)", "display"),
    ("console decision",
     "> Decided in the console by Synthbridge (tenant owner)",
     "> Decided in the console by [the CEO] (tenant owner)", "surname"),
    ("run actor",
     "| Run | actor `synthwood-synth` |",
     "| Run | actor `[a person]` |", "login"),
]

IDS = [s[0] for s in SHAPES]


@pytest.mark.parametrize("shape,raw,want,kind", SHAPES, ids=IDS)
def test_each_shape_is_redacted_to_its_role(shape, raw, want, kind):
    assert name_redact.redact(raw, roster()) == want


@pytest.mark.parametrize("shape,raw,want,kind", SHAPES, ids=IDS)
def test_each_shape_unredacted_is_refused_naming_file_line_and_kind(shape, raw, want, kind):
    r = roster()
    lines = [("architecture/proofs/dre-6673.md", 1, "# Proof record"),
             ("architecture/proofs/dre-6673.md", 2, raw)]
    assert name_redact.findings(lines, r) == [
        name_redact.Finding("architecture/proofs/dre-6673.md", 2, kind)]
    assert name_redact.findings([("architecture/proofs/dre-6673.md", 2, want)], r) == []


def push(tmp_path, line: str):
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "agent/proof")
    head = commit(repo, {"architecture/proofs/dre-6673.md": f"# Proof record\n\n{line}\n"},
                  "the proof record")
    stdin = f"refs/heads/agent/proof {head} refs/heads/agent/proof {ZERO}\n"
    argv = ["guard", "--pre-push", "--default-ref", "main", "--cwd", str(repo), "--owner", OWNER]
    return run(argv, env=LIVE, stdin=stdin)


@pytest.mark.parametrize("shape,raw,want,kind", SHAPES, ids=IDS)
def test_the_live_guard_refuses_an_unredacted_push_of_each(shape, raw, want, kind, tmp_path):
    code, out, err = push(tmp_path, raw)
    assert code == 1
    assert out.splitlines()[1:] == [
        f"name-guard: refused — architecture/proofs/dre-6673.md:3 carries a {kind} form"]
    no_secret(out + err)


@pytest.mark.parametrize("shape,raw,want,kind", SHAPES, ids=IDS)
def test_the_live_guard_passes_the_redacted_push_of_each(shape, raw, want, kind, tmp_path):
    code, out, _ = push(tmp_path, want)
    assert code == 0
    assert out.splitlines()[1:] == ["name-guard: ok — 4 lines read, nothing to refuse"]
